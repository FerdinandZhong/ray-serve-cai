import io
import shlex
import tarfile
from types import SimpleNamespace

import pytest

from cai_integration import setup_environment
from ray_serve_cai.scripts import start_nginx


def test_nginx_capability_check_requires_http_ssl_module(monkeypatch, tmp_path):
    binary = tmp_path / "nginx"
    binary.write_text("binary")
    monkeypatch.setattr(
        setup_environment.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="configure --without-http_rewrite_module"),
    )
    assert not setup_environment._nginx_has_ssl(str(binary))
    monkeypatch.setattr(
        setup_environment.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="configure --with-http_ssl_module"),
    )
    assert setup_environment._nginx_has_ssl(str(binary))


def test_grafana_proxy_refuses_http_only_nginx(monkeypatch, tmp_path):
    binary = tmp_path / "nginx"
    binary.write_text("binary")
    binary.chmod(0o755)
    monkeypatch.setenv("GRAFANA_PROXY_UPSTREAM", "https://grafana.example.test")
    monkeypatch.setattr(start_nginx, "NGINX_CANDIDATES", [str(binary)])

    def run(command, **kwargs):
        if command[0] == "which":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="configure --without-http_rewrite_module")

    monkeypatch.setattr(start_nginx.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="SSL-capable nginx"):
        start_nginx.find_nginx()


def test_grafana_proxy_selects_ssl_capable_nginx(monkeypatch, tmp_path):
    binary = tmp_path / "nginx-ssl"
    binary.write_text("binary")
    binary.chmod(0o755)
    monkeypatch.setenv("GRAFANA_PROXY_UPSTREAM", "https://grafana.example.test")
    monkeypatch.setattr(start_nginx, "NGINX_CANDIDATES", [str(binary)])
    monkeypatch.setattr(
        start_nginx.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="configure --with-http_ssl_module"),
    )
    assert start_nginx.find_nginx() == str(binary)


def test_ssl_source_build_mime_types_are_found(monkeypatch):
    expected = "/home/cdsw/.local/nginx-ssl/conf/mime.types"
    monkeypatch.setattr(start_nginx.os.path, "isfile", lambda path: path == expected)
    assert start_nginx.find_mime_types() == expected


def test_source_install_enables_https_proxy_module(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_environment.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_environment.shutil, "which", lambda _: None)
    monkeypatch.setenv("NGINX_VERSION", "1.29.7")
    binary = tmp_path / ".local/bin/nginx-ssl"
    monkeypatch.setattr(setup_environment, "_nginx_has_ssl", lambda _: binary.exists())
    commands = []

    def run(command, cwd=None):
        commands.append(command)
        if command.startswith("curl -fsSL -o "):
            archive = shlex.split(command)[3]
            with tarfile.open(archive, "w:gz") as tar:
                directory = tarfile.TarInfo("nginx-1.29.7/")
                directory.type = tarfile.DIRTYPE
                tar.addfile(directory, io.BytesIO())
        if command == "make install":
            binary.write_text("nginx")
        return True

    monkeypatch.setattr(setup_environment, "run_command", run)
    assert setup_environment.install_nginx()
    assert any("--with-http_ssl_module" in command for command in commands)
