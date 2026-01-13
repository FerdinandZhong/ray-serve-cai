"""
Ray Backend Orchestrator for vLLM Service
Manages Ray cluster initialization and vLLM deployment lifecycle
"""

import asyncio
import logging
import socket
import subprocess
import time
from typing import Optional, Dict, Any, AsyncIterator

logger = logging.getLogger(__name__)


class RayBackend:
    """
    Ray backend orchestrator for vLLM service.

    Manages:
    - Ray cluster initialization (local or remote)
    - vLLM deployment lifecycle (start/stop)
    - Status monitoring and logging
    """

    def __init__(self):
        """Initialize Ray backend."""
        self._ray_initialized = False
        self._deployment_running = False
        self._application_name = "vllm-app"

        # Check Ray availability once during initialization
        self._check_ray_availability()

    def _check_ray_availability(self):
        """
        Check if Ray and Ray Serve are available.

        This is called once during initialization to determine if
        Ray backend can be used.
        """
        try:
            import ray
            from ray import serve

            self.ray = ray
            self.serve = serve
            self.ray_available = True
            logger.info("✅ Ray and Ray Serve are available")

        except ImportError as e:
            self.ray_available = False
            logger.warning(f"❌ Ray not available: {e}")
            logger.warning("Install with: pip install 'ray[serve]'")

    def _detect_existing_ray_cluster(self) -> Optional[str]:
        """
        Detect if a Ray cluster is already running.

        Returns:
            Ray cluster address if detected, None otherwise
        """
        # Method 1: Check default Ray port (6379)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', 6379))
            sock.close()

            if result == 0:
                logger.info("Detected Ray cluster on default port 6379")
                return "127.0.0.1:6379"
        except Exception as e:
            logger.debug(f"Port check failed: {e}")

        # Method 2: Try ray status command
        try:
            result = subprocess.run(
                ["ray", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False
            )

            if result.returncode == 0 and "No cluster running" not in result.stdout:
                logger.info("Detected Ray cluster via 'ray status'")
                # Let Ray auto-detect the address
                return "auto"
        except Exception as e:
            logger.debug(f"'ray status' check failed: {e}")

        return None

    async def initialize_ray(self, address: Optional[str] = None) -> Dict[str, Any]:
        """
        Initialize Ray cluster - connect to existing or start local.

        Args:
            address: Ray cluster address to connect to.
                     If None, auto-detect existing cluster or start local.

        Returns:
            Dictionary with initialization status
        """
        if not self.ray_available:
            return {
                'initialized': False,
                'error': 'ray_not_installed',
                'message': 'Ray is not installed. Install with: pip install ray[serve]'
            }

        try:
            # Check if Ray is already initialized
            if self.ray.is_initialized():
                logger.info("Ray is already initialized")
                self._ray_initialized = True

                return {
                    'initialized': True,
                    'mode': 'already_initialized',
                    'address': self.ray.get_runtime_context().gcs_address,
                    'nodes': len(self.ray.nodes())
                }

            # Auto-detect existing cluster if no address specified
            if address is None:
                detected_address = self._detect_existing_ray_cluster()
                if detected_address:
                    address = detected_address
                    logger.info(f"Auto-detected Ray cluster: {address}")

            # Initialize Ray
            if address:
                # Connect to existing cluster
                logger.info(f"Connecting to Ray cluster at: {address}")
                self.ray.init(
                    address=address,
                    runtime_env={"pip": ["vllm"]},
                    ignore_reinit_error=True
                )
                mode = 'connected'
            else:
                # Start local cluster
                logger.info("Starting local Ray cluster")
                self.ray.init(
                    runtime_env={"pip": ["vllm"]},
                    ignore_reinit_error=True
                )
                mode = 'local'

            self._ray_initialized = True

            # Get cluster info
            nodes = self.ray.nodes()
            gcs_address = self.ray.get_runtime_context().gcs_address

            logger.info(f"✅ Ray cluster initialized")
            logger.info(f"   Mode: {mode}")
            logger.info(f"   Address: {gcs_address}")
            logger.info(f"   Nodes: {len(nodes)}")

            return {
                'initialized': True,
                'mode': mode,
                'address': gcs_address,
                'nodes': len(nodes),
                'node_info': [
                    {
                        'node_id': node.get('NodeID'),
                        'alive': node.get('Alive'),
                        'resources': node.get('Resources', {})
                    }
                    for node in nodes
                ]
            }

        except Exception as e:
            logger.error(f"Failed to initialize Ray: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'initialized': False,
                'error': str(e),
                'message': f'Failed to initialize Ray cluster: {e}'
            }

    async def start_model(
        self,
        vllm_config: Dict[str, Any],
        wait_ready: bool = False,
        engine: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Start LLM model deployment using Ray Serve.

        Args:
            vllm_config: Configuration dictionary (engine-specific)
            wait_ready: If True, wait for deployment to be ready
            engine: Engine type to use ("vllm", "sglang", etc.).
                   If None, uses: explicit param → config['engine'] → default

        Returns:
            Dictionary with deployment status
        """
        if not self.ray_available:
            return {
                'started': False,
                'error': 'ray_not_installed',
                'message': 'Ray is not installed'
            }

        try:
            # Initialize Ray if not already done
            if not self._ray_initialized:
                init_result = await self.initialize_ray()
                if not init_result['initialized']:
                    return {
                        'started': False,
                        'error': 'ray_init_failed',
                        'message': init_result.get('message', 'Failed to initialize Ray')
                    }

            # Determine engine type
            # Priority: explicit param → config field → default engine
            from .engines.registry import get_registry

            registry = get_registry()

            if engine is None:
                engine = vllm_config.get('engine') or registry.get_default_engine()

            if engine is None:
                return {
                    'started': False,
                    'error': 'no_engine_specified',
                    'message': 'No engine specified and no default engine available'
                }

            # Get engine components from registry
            try:
                config_builder = registry.get_config_builder(engine)
                deployment_factory = registry.get_deployment_factory(engine)
            except KeyError as e:
                available = ", ".join(registry.list_engines())
                return {
                    'started': False,
                    'error': 'invalid_engine',
                    'message': f'Engine "{engine}" not found. Available: {available}'
                }

            # Validate configuration
            is_valid, error_msg = config_builder.validate_config(vllm_config)
            if not is_valid:
                return {
                    'started': False,
                    'error': 'invalid_config',
                    'message': error_msg
                }

            # Build engine configuration
            engine_config = config_builder.build_config(vllm_config)

            # Extract deployment parameters
            tensor_parallel_size = vllm_config.get('tensor_parallel_size', 1)
            use_cpu = vllm_config.get('use_cpu', False)
            port = vllm_config.get('port', 8000)

            logger.info(f"Starting {engine.upper()} deployment via Ray Serve")
            logger.info(f"   Engine: {engine}")
            logger.info(f"   Model: {engine_config['model']}")
            logger.info(f"   Tensor Parallel Size: {tensor_parallel_size}")
            logger.info(f"   CPU Mode: {use_cpu}")
            logger.info(f"   Port: {port}")

            # Create deployment using factory
            deployment = deployment_factory.create_deployment(
                engine_config=engine_config,
                num_replicas=1,
                tensor_parallel_size=tensor_parallel_size,
                use_cpu=use_cpu
            )

            # Start Ray Serve if not running
            try:
                self.serve.start(
                    http_options={
                        "host": "0.0.0.0",
                        "port": port
                    }
                )
            except Exception as e:
                # Serve might already be running
                logger.debug(f"Ray Serve start: {e}")

            # Deploy application
            self.serve.run(
                deployment,
                name=self._application_name,
                route_prefix="/v1"
            )

            self._deployment_running = True

            logger.info(f"✅ {engine.upper()} deployment started")

            result = {
                'started': True,
                'engine': engine,
                'application_name': self._application_name,
                'port': port,
                'status': 'running',
                'model': engine_config['model']
            }

            # Wait for readiness if requested
            if wait_ready:
                readiness = await self.wait_for_ready(port=port)
                result.update(readiness)

            return result

        except Exception as e:
            logger.error(f"Failed to start vLLM deployment: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'started': False,
                'error': str(e),
                'message': f'Failed to start deployment: {e}'
            }

    async def wait_for_ready(
        self,
        port: int = 8000,
        timeout: int = 120
    ) -> Dict[str, Any]:
        """
        Wait for vLLM deployment to be ready.

        Args:
            port: Port where vLLM is listening
            timeout: Maximum wait time in seconds

        Returns:
            Dictionary with readiness status
        """
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not available - skipping readiness check")
            return {'ready': False, 'error': 'aiohttp not installed'}

        logger.info(f"Waiting for vLLM deployment to be ready (timeout: {timeout}s)")
        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                # Check Ray Serve application status
                app_status = self.serve.status()
                if self._application_name in app_status.applications:
                    app_info = app_status.applications[self._application_name]

                    if app_info.status == "RUNNING":
                        # Try health check endpoint
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                f"http://localhost:{port}/health",
                                timeout=aiohttp.ClientTimeout(total=3)
                            ) as response:
                                if response.status == 200:
                                    elapsed = time.time() - start_time
                                    logger.info(f"✅ Deployment ready (took {elapsed:.1f}s)")
                                    return {
                                        'ready': True,
                                        'elapsed_time': round(elapsed, 1)
                                    }

            except aiohttp.ClientError as e:
                last_error = f"Connection error: {type(e).__name__}"
            except asyncio.TimeoutError:
                last_error = "Request timeout"
            except Exception as e:
                last_error = str(e)

            # Wait before retry
            elapsed = time.time() - start_time
            if elapsed < timeout:
                await asyncio.sleep(5)
                if int(elapsed) % 15 == 0:
                    logger.info(f"Still waiting... ({int(elapsed)}s elapsed)")

        # Timeout reached
        elapsed = time.time() - start_time
        logger.warning(f"❌ Timeout waiting for deployment ({elapsed:.1f}s)")

        return {
            'ready': False,
            'error': 'timeout',
            'elapsed_time': round(elapsed, 1),
            'last_error': last_error
        }

    async def stop_model(self) -> Dict[str, Any]:
        """
        Stop vLLM deployment.

        Returns:
            Dictionary with stop status
        """
        if not self.ray_available:
            return {
                'stopped': False,
                'error': 'ray_not_installed'
            }

        try:
            if not self._ray_initialized or not self.ray.is_initialized():
                logger.info("Ray not initialized - nothing to stop")
                return {
                    'stopped': True,
                    'status': 'not_running'
                }

            # Check if application exists
            app_status = self.serve.status()
            if self._application_name not in app_status.applications:
                logger.info(f"Application {self._application_name} not found")
                return {
                    'stopped': True,
                    'status': 'not_running'
                }

            # Delete application
            logger.info(f"Stopping application: {self._application_name}")
            self.serve.delete(self._application_name)

            self._deployment_running = False

            logger.info(f"✅ Application stopped")

            return {
                'stopped': True,
                'status': 'stopped'
            }

        except Exception as e:
            logger.error(f"Error stopping deployment: {e}")
            import traceback
            logger.error(traceback.format_exc())

            return {
                'stopped': False,
                'error': str(e),
                'status': 'error'
            }

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current deployment status.

        Returns:
            Dictionary with deployment status
        """
        if not self.ray_available:
            return {
                'running': False,
                'status': 'ray_not_available'
            }

        try:
            if not self._ray_initialized or not self.ray.is_initialized():
                return {
                    'running': False,
                    'status': 'ray_not_initialized'
                }

            # Get application status
            app_status = self.serve.status()

            if self._application_name not in app_status.applications:
                return {
                    'running': False,
                    'status': 'not_deployed'
                }

            app_info = app_status.applications[self._application_name]

            return {
                'running': app_info.status == "RUNNING",
                'status': app_info.status.lower(),
                'application_name': self._application_name,
                'deployments': [
                    {
                        'name': dep_name,
                        'status': dep_info.status,
                        'message': dep_info.message
                    }
                    for dep_name, dep_info in app_info.deployments.items()
                ]
            }

        except Exception as e:
            logger.error(f"Error checking status: {e}")
            return {
                'running': False,
                'status': 'error',
                'error': str(e)
            }

    async def get_logs(self, tail: int = 100) -> AsyncIterator[str]:
        """
        Get deployment logs.

        Args:
            tail: Number of recent log lines to fetch

        Yields:
            Log lines from deployment
        """
        if not self.ray_available or not self._ray_initialized:
            yield "[ERROR] Ray not initialized"
            return

        try:
            yield f"[INFO] Fetching logs for application: {self._application_name}"

            # Get application status
            status = await self.get_status()
            yield f"[INFO] Application status: {status.get('status', 'unknown')}"

            if status.get('deployments'):
                for dep in status['deployments']:
                    yield f"[INFO] Deployment {dep['name']}: {dep['status']}"
                    if dep.get('message'):
                        yield f"       Message: {dep['message']}"

            # Try to get Ray logs directory
            try:
                if hasattr(self.ray, 'worker') and hasattr(self.ray.worker, 'global_worker'):
                    log_dir = self.ray.worker.global_worker.node.get_logs_dir_path()
                    yield f"[INFO] Ray logs directory: {log_dir}"
            except Exception as e:
                yield f"[WARNING] Could not access Ray logs: {e}"

        except Exception as e:
            logger.error(f"Error fetching logs: {e}")
            yield f"[ERROR] Failed to fetch logs: {e}"

    async def shutdown(self):
        """
        Shutdown Ray backend.

        Stops all deployments and optionally shuts down Ray Serve.
        Note: Does not shut down the Ray cluster itself.
        """
        if not self.ray_available:
            return

        try:
            # Stop deployment
            await self.stop_model()

            # Optionally shutdown Ray Serve
            if self._ray_initialized and self.ray.is_initialized():
                try:
                    self.serve.shutdown()
                    logger.info("Ray Serve shutdown complete")
                except Exception as e:
                    logger.warning(f"Error shutting down Ray Serve: {e}")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    def close(self):
        """Close Ray backend - sync wrapper for shutdown."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.shutdown())
            else:
                loop.run_until_complete(self.shutdown())
        except Exception as e:
            logger.error(f"Error closing Ray backend: {e}")


# Global Ray backend instance
ray_backend = RayBackend()
