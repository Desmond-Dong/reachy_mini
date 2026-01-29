"""Daemon-related API routes."""

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from reachy_mini.daemon.app import bg_job_register

from ...daemon import Daemon, DaemonStatus
from ..dependencies import get_daemon

router = APIRouter(
    prefix="/daemon",
)
busy_lock = threading.Lock()


@router.post("/start")
async def start_daemon(
    request: Request,
    wake_up: bool,
    daemon: Daemon = Depends(get_daemon),
) -> dict[str, str]:
    """Start the daemon."""
    if busy_lock.locked():
        raise HTTPException(status_code=409, detail="Daemon is busy.")

    async def start(logger: logging.Logger) -> None:
        with busy_lock:
            await daemon.start(
                sim=request.app.state.args.sim,
                serialport=request.app.state.args.serialport,
                scene=request.app.state.args.scene,
                localhost_only=request.app.state.args.localhost_only,
                wake_up_on_start=wake_up,
                check_collision=request.app.state.args.check_collision,
                kinematics_engine=request.app.state.args.kinematics_engine,
                headless=request.app.state.args.headless,
                websocket_uri=request.app.state.args.websocket_uri,
                stream_media=request.app.state.args.stream_media,
                use_audio=request.app.state.args.use_audio,
                hardware_config_filepath=request.app.state.args.hardware_config_filepath,
            )

    job_id = bg_job_register.run_command("daemon-start", start)
    return {"job_id": job_id}


@router.post("/stop")
async def stop_daemon(
    goto_sleep: bool, daemon: Daemon = Depends(get_daemon)
) -> dict[str, str]:
    """Stop the daemon, optionally putting the robot to sleep."""
    if busy_lock.locked():
        raise HTTPException(status_code=409, detail="Daemon is busy.")

    async def stop(logger: logging.Logger) -> None:
        with busy_lock:
            await daemon.stop(goto_sleep_on_stop=goto_sleep)

    job_id = bg_job_register.run_command("daemon-stop", stop)
    return {"job_id": job_id}


@router.post("/restart")
async def restart_daemon(
    request: Request, daemon: Daemon = Depends(get_daemon)
) -> dict[str, str]:
    """Restart the daemon."""
    if busy_lock.locked():
        raise HTTPException(status_code=409, detail="Daemon is busy.")

    async def restart(logger: logging.Logger) -> None:
        with busy_lock:
            await daemon.restart()

    job_id = bg_job_register.run_command("daemon-restart", restart)
    return {"job_id": job_id}


@router.get("/status")
async def get_daemon_status(daemon: Daemon = Depends(get_daemon)) -> DaemonStatus:
    """Get the current status of the daemon."""
    return daemon.status()


@router.get("/health")
async def health_check(daemon: Daemon = Depends(get_daemon)) -> dict[str, Any]:
    """Detailed health check endpoint."""
    status = daemon.status()
    backend_info = None
    if status.backend_status:
        backend_info = {
            "type": type(status.backend_status).__name__,
            "motor_control_mode": status.backend_status.motor_control_mode,
            "error": status.backend_status.error,
        }

    return {
        "status": "healthy" if status.state.value in ["running", "idle"] else "unhealthy",
        "daemon_status": status.state.value,
        "daemon_state": status.state,
        "backend_status": backend_info,
        "timestamp": time.time(),
        "wireless_version": status.wireless_version,
        "simulation_enabled": status.simulation_enabled,
        "error": status.error,
    }


@router.get("/health/ready")
async def readiness_check() -> dict[str, str]:
    """Readiness probe - HTTP server is ready."""
    return {"status": "ready"}


@router.get("/health/live")
async def liveness_check(daemon: Daemon = Depends(get_daemon)) -> dict[str, Any]:
    """Liveness probe - daemon is alive."""
    status = daemon.status()
    if status.state.value in ["running", "idle"]:
        return {"status": "alive", "daemon_state": status.state.value}
    else:
        raise HTTPException(
            status_code=503,
            detail=f"Daemon not running, state: {status.state.value}, error: {status.error}"
        )
