"""Two-pod TCPStore, Gloo, and NCCL diagnostic; run with the fish companion."""

import argparse
import signal
import socket
from datetime import timedelta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rank", type=int, choices=(0, 1), required=True)
    parser.add_argument("--master", required=True)
    parser.add_argument("--port", type=int, default=29601)
    args = parser.parse_args()

    # Default SIGALRM action terminates even when native code is stuck.
    signal.alarm(180)
    import torch
    import torch.distributed as dist

    def log(message):
        print(f"RANK {args.rank}: {message}", flush=True)

    timeout = timedelta(seconds=45)
    log(f"host={socket.gethostname()} interfaces={socket.if_nameindex()}")
    log(f"torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable in diagnostic process")
    torch.cuda.set_device(0)
    log(f"GPU={torch.cuda.get_device_name(0)}")

    log(f"START TCPStore {args.master}:{args.port}")
    store = dist.TCPStore(
        args.master, args.port, 2, args.rank == 0,
        timeout=timeout, wait_for_workers=True,
    )
    store.set(f"probe/{args.rank}", "ready")
    if store.get(f"probe/{1 - args.rank}") != b"ready":
        raise RuntimeError("Unexpected TCPStore response")
    log("PASS TCPStore exchange")

    log("START Gloo")
    dist.init_process_group(
        backend="gloo", store=dist.PrefixStore("gloo-probe", store),
        rank=args.rank, world_size=2, timeout=timeout,
    )
    cpu = torch.tensor([float(args.rank + 1)])
    dist.all_reduce(cpu)
    if cpu.item() != 3.0:
        raise RuntimeError(f"Incorrect Gloo result: {cpu}")
    log("PASS Gloo all_reduce = 3")

    log("START NCCL")
    gpu_group = dist.new_group(ranks=[0, 1], backend="nccl", timeout=timeout)
    gpu = torch.tensor([float(args.rank + 1)], device="cuda")
    dist.all_reduce(gpu, group=gpu_group)
    torch.cuda.synchronize()
    if gpu.item() != 3.0:
        raise RuntimeError(f"Incorrect NCCL result: {gpu}")
    log("PASS NCCL all_reduce = 3")

    dist.barrier()
    dist.destroy_process_group(gpu_group)
    dist.destroy_process_group()
    log("DONE")
    # Keep the alarm armed through native-library process teardown.


if __name__ == "__main__":
    main()
