"""
Sliver Client Service - Wrapper around SliverPy
"""

import asyncio
import logging
import os
from typing import Optional, List, Any
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import SliverConnectionError, SliverCommandError

logger = logging.getLogger(__name__)

# Try to import sliver-py
try:
    from sliver import SliverClient, SliverClientConfig
    SLIVER_AVAILABLE = True
except ImportError:
    SLIVER_AVAILABLE = False
    logger.warning("sliver-py not installed - Sliver features will be unavailable")


class SliverManager:
    """
    Manages connection to Sliver server via gRPC
    """

    def __init__(self):
        self._client: Optional[Any] = None
        self._config_path: Optional[str] = None
        self._connected: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if connected to Sliver"""
        return self._connected and self._client is not None

    @property
    def client(self) -> Any:
        """Get Sliver client instance"""
        if not self._client:
            raise SliverConnectionError("Not connected to Sliver server")
        return self._client

    async def connect(self, config_path: Optional[str] = None) -> None:
        """Connect to Sliver server"""
        if not SLIVER_AVAILABLE:
            raise SliverConnectionError("sliver-py is not installed")

        async with self._lock:
            if self._connected:
                return

            config_path = config_path or settings.sliver_config
            if not config_path:
                raise SliverConnectionError("No Sliver config file specified")

            config_file = Path(config_path)
            if not config_file.exists():
                raise SliverConnectionError(f"Config file not found: {config_path}")

            try:
                # Parse config file
                config = SliverClientConfig.parse_config_file(str(config_file))

                # Create and connect client
                self._client = SliverClient(config)
                await self._client.connect()

                self._config_path = config_path
                self._connected = True
                logger.info(f"Connected to Sliver server")

            except Exception as e:
                self._client = None
                self._connected = False
                raise SliverConnectionError(f"Failed to connect: {str(e)}")

    async def disconnect(self) -> None:
        """Disconnect from Sliver server"""
        async with self._lock:
            if self._client:
                try:
                    # SliverPy doesn't have explicit disconnect, but we clean up
                    self._client = None
                except Exception as e:
                    logger.error(f"Error disconnecting: {e}")
                finally:
                    self._connected = False

    async def reconnect(self) -> None:
        """Reconnect to Sliver server"""
        await self.disconnect()
        await self.connect(self._config_path)

    # ═══════════════════════════════════════════════════════════════════════════
    # Session Operations
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_sessions(self) -> List[dict]:
        """Get all active sessions"""
        if not self.is_connected:
            return []

        try:
            sessions = await self._client.sessions()
            return [self._session_to_dict(s) for s in sessions]
        except Exception as e:
            logger.error(f"Failed to get sessions: {e}")
            raise SliverCommandError(f"Failed to get sessions: {str(e)}")

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get specific session by ID"""
        sessions = await self.get_sessions()
        for session in sessions:
            if session["id"] == session_id:
                return session
        return None

    async def kill_session(self, session_id: str) -> bool:
        """Kill a session"""
        try:
            session = await self._client.interact_session(session_id)
            await session.kill()
            return True
        except Exception as e:
            logger.error(f"Failed to kill session {session_id}: {e}")
            raise SliverCommandError(f"Failed to kill session: {str(e)}")

    async def session_shell(
        self, session_id: str, command: str, timeout: int = 60
    ) -> dict:
        """Execute shell command on session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await asyncio.wait_for(
                session.execute(command, output=True),
                timeout=timeout
            )
            return {
                "output": result.Stdout.decode() if result.Stdout else "",
                "stderr": result.Stderr.decode() if result.Stderr else "",
                "exit_code": result.Status,
            }
        except asyncio.TimeoutError:
            raise SliverCommandError(f"Command timed out after {timeout}s")
        except Exception as e:
            logger.error(f"Shell command failed: {e}")
            raise SliverCommandError(f"Command failed: {str(e)}")

    async def session_ps(self, session_id: str) -> List[dict]:
        """Get process list from session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.ps()
            return [
                {
                    "pid": p.Pid,
                    "ppid": p.Ppid,
                    "executable": p.Executable,
                    "owner": p.Owner,
                    "architecture": p.Architecture,
                }
                for p in result.Processes
            ]
        except Exception as e:
            raise SliverCommandError(f"Failed to get process list: {str(e)}")

    async def session_ls(self, session_id: str, path: str) -> dict:
        """List directory on session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.ls(path)
            return {
                "path": result.Path,
                "files": [
                    {
                        "name": f.Name,
                        "is_dir": f.IsDir,
                        "size": f.Size,
                        "mode": f.Mode,
                    }
                    for f in result.Files
                ],
            }
        except Exception as e:
            raise SliverCommandError(f"Failed to list directory: {str(e)}")

    async def session_download(self, session_id: str, remote_path: str) -> bytes:
        """Download file from session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.download(remote_path)
            return result.Data
        except Exception as e:
            raise SliverCommandError(f"Failed to download: {str(e)}")

    async def session_upload(
        self, session_id: str, remote_path: str, data: bytes
    ) -> bool:
        """Upload file to session"""
        try:
            session = await self._client.interact_session(session_id)
            await session.upload(remote_path, data)
            return True
        except Exception as e:
            raise SliverCommandError(f"Failed to upload: {str(e)}")

    async def session_screenshot(self, session_id: str) -> bytes:
        """Take screenshot from session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.screenshot()
            return result.Data
        except Exception as e:
            raise SliverCommandError(f"Failed to take screenshot: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Beacon Operations
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_beacons(self) -> List[dict]:
        """Get all beacons"""
        if not self.is_connected:
            return []

        try:
            beacons = await self._client.beacons()
            return [self._beacon_to_dict(b) for b in beacons]
        except Exception as e:
            logger.error(f"Failed to get beacons: {e}")
            raise SliverCommandError(f"Failed to get beacons: {str(e)}")

    async def get_beacon(self, beacon_id: str) -> Optional[dict]:
        """Get specific beacon"""
        beacons = await self.get_beacons()
        for beacon in beacons:
            if beacon["id"] == beacon_id:
                return beacon
        return None

    async def kill_beacon(self, beacon_id: str) -> bool:
        """Kill a beacon"""
        try:
            await self._client.rm_beacon(beacon_id)
            return True
        except Exception as e:
            raise SliverCommandError(f"Failed to kill beacon: {str(e)}")

    async def get_beacon_tasks(self, beacon_id: str) -> List[dict]:
        """Get tasks for a beacon"""
        try:
            tasks = await self._client.beacon_tasks(beacon_id)
            return [self._task_to_dict(t) for t in tasks]
        except Exception as e:
            raise SliverCommandError(f"Failed to get beacon tasks: {str(e)}")

    async def beacon_shell(self, beacon_id: str, command: str) -> dict:
        """Queue shell command on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            task = await beacon.execute(command, output=True)
            return {"task_id": task.TaskID, "beacon_id": beacon_id, "command": command}
        except Exception as e:
            raise SliverCommandError(f"Failed to queue shell task: {str(e)}")

    async def beacon_download(self, beacon_id: str, remote_path: str) -> dict:
        """Queue download task on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            task = await beacon.download(remote_path)
            return {"task_id": task.TaskID, "beacon_id": beacon_id, "path": remote_path}
        except Exception as e:
            raise SliverCommandError(f"Failed to queue download task: {str(e)}")

    async def beacon_upload(self, beacon_id: str, remote_path: str, data: bytes) -> dict:
        """Queue upload task on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            task = await beacon.upload(remote_path, data)
            return {"task_id": task.TaskID, "beacon_id": beacon_id, "path": remote_path}
        except Exception as e:
            raise SliverCommandError(f"Failed to queue upload task: {str(e)}")

    async def beacon_ps(self, beacon_id: str) -> dict:
        """Queue process list task on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            task = await beacon.ps()
            return {"task_id": task.TaskID, "beacon_id": beacon_id}
        except Exception as e:
            raise SliverCommandError(f"Failed to queue ps task: {str(e)}")

    async def beacon_screenshot(self, beacon_id: str) -> dict:
        """Queue screenshot task on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            task = await beacon.screenshot()
            return {"task_id": task.TaskID, "beacon_id": beacon_id}
        except Exception as e:
            raise SliverCommandError(f"Failed to queue screenshot task: {str(e)}")

    async def get_task_result(self, beacon_id: str, task_id: str) -> Optional[dict]:
        """Get result of a beacon task"""
        try:
            tasks = await self._client.beacon_tasks(beacon_id)
            for task in tasks:
                if str(task.ID) == task_id:
                    return self._task_to_dict(task)
            return None
        except Exception as e:
            raise SliverCommandError(f"Failed to get task result: {str(e)}")

    def _task_to_dict(self, task: Any) -> dict:
        """Convert Sliver task object to dict"""
        return {
            "id": str(task.ID),
            "beacon_id": task.BeaconID,
            "created_at": task.CreatedAt,
            "state": task.State,
            "sent_at": task.SentAt if hasattr(task, 'SentAt') else None,
            "completed_at": task.CompletedAt if hasattr(task, 'CompletedAt') else None,
            "request": task.Request if hasattr(task, 'Request') else None,
            "response": task.Response if hasattr(task, 'Response') else None,
            "description": task.Description if hasattr(task, 'Description') else None,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Pivoting Operations (SOCKS & Port Forwarding)
    # ═══════════════════════════════════════════════════════════════════════════

    async def start_socks_proxy(self, session_id: str, host: str = "127.0.0.1", port: int = 1080) -> dict:
        """Start SOCKS5 proxy through session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.socks5(host=host, port=port)
            return {
                "id": str(result.TunnelID) if hasattr(result, 'TunnelID') else str(port),
                "host": host,
                "port": port,
                "session_id": session_id,
                "type": "socks5",
            }
        except Exception as e:
            raise SliverCommandError(f"Failed to start SOCKS proxy: {str(e)}")

    async def stop_socks_proxy(self, session_id: str, tunnel_id: int) -> bool:
        """Stop SOCKS5 proxy"""
        try:
            session = await self._client.interact_session(session_id)
            await session.close_socks(tunnel_id)
            return True
        except Exception as e:
            raise SliverCommandError(f"Failed to stop SOCKS proxy: {str(e)}")

    async def start_portfwd(
        self,
        session_id: str,
        remote_host: str,
        remote_port: int,
        local_host: str = "127.0.0.1",
        local_port: int = 0,
    ) -> dict:
        """Start port forwarding through session"""
        try:
            session = await self._client.interact_session(session_id)
            result = await session.portfwd(
                remote_host=remote_host,
                remote_port=remote_port,
                local_host=local_host,
                local_port=local_port,
            )
            return {
                "id": str(result.TunnelID) if hasattr(result, 'TunnelID') else f"{local_port}",
                "local_host": local_host,
                "local_port": result.LocalPort if hasattr(result, 'LocalPort') else local_port,
                "remote_host": remote_host,
                "remote_port": remote_port,
                "session_id": session_id,
                "type": "portfwd",
            }
        except Exception as e:
            raise SliverCommandError(f"Failed to start port forwarding: {str(e)}")

    async def stop_portfwd(self, session_id: str, tunnel_id: int) -> bool:
        """Stop port forwarding"""
        try:
            session = await self._client.interact_session(session_id)
            await session.close_portfwd(tunnel_id)
            return True
        except Exception as e:
            raise SliverCommandError(f"Failed to stop port forwarding: {str(e)}")

    async def list_pivots(self, session_id: str) -> List[dict]:
        """List all active pivots (socks + port forwards) for a session"""
        try:
            session = await self._client.interact_session(session_id)
            # Get SOCKS proxies
            socks_list = []
            try:
                socks = await session.list_socks()
                socks_list = [
                    {
                        "id": str(s.TunnelID),
                        "type": "socks5",
                        "host": s.Host,
                        "port": s.Port,
                    }
                    for s in socks
                ]
            except:
                pass

            # Get port forwards
            portfwd_list = []
            try:
                portfwds = await session.list_portfwd()
                portfwd_list = [
                    {
                        "id": str(p.TunnelID),
                        "type": "portfwd",
                        "local_host": p.LocalHost,
                        "local_port": p.LocalPort,
                        "remote_host": p.RemoteHost,
                        "remote_port": p.RemotePort,
                    }
                    for p in portfwds
                ]
            except:
                pass

            return socks_list + portfwd_list
        except Exception as e:
            raise SliverCommandError(f"Failed to list pivots: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Listener/Job Operations
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_jobs(self) -> List[dict]:
        """Get all active jobs (listeners)"""
        if not self.is_connected:
            return []

        try:
            jobs = await self._client.jobs()
            return [self._job_to_dict(j) for j in jobs]
        except Exception as e:
            raise SliverCommandError(f"Failed to get jobs: {str(e)}")

    async def start_mtls_listener(self, host: str, port: int) -> dict:
        """Start mTLS listener"""
        try:
            job = await self._client.start_mtls_listener(host=host, port=port)
            return self._job_to_dict(job)
        except Exception as e:
            raise SliverCommandError(f"Failed to start mTLS listener: {str(e)}")

    async def start_https_listener(
        self, host: str, port: int, domain: str, **kwargs
    ) -> dict:
        """Start HTTPS listener"""
        try:
            job = await self._client.start_https_listener(
                host=host, port=port, domain=domain, **kwargs
            )
            return self._job_to_dict(job)
        except Exception as e:
            raise SliverCommandError(f"Failed to start HTTPS listener: {str(e)}")

    async def start_http_listener(self, host: str, port: int, **kwargs) -> dict:
        """Start HTTP listener"""
        try:
            job = await self._client.start_http_listener(
                host=host, port=port, **kwargs
            )
            return self._job_to_dict(job)
        except Exception as e:
            raise SliverCommandError(f"Failed to start HTTP listener: {str(e)}")

    async def start_dns_listener(
        self, domains: List[str], host: str, port: int, **kwargs
    ) -> dict:
        """Start DNS listener"""
        try:
            job = await self._client.start_dns_listener(
                domains=domains, host=host, port=port, **kwargs
            )
            return self._job_to_dict(job)
        except Exception as e:
            raise SliverCommandError(f"Failed to start DNS listener: {str(e)}")

    async def kill_job(self, job_id: int) -> bool:
        """Kill a job/listener"""
        try:
            await self._client.kill_job(job_id)
            return True
        except Exception as e:
            raise SliverCommandError(f"Failed to kill job: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Implant Generation
    # ═══════════════════════════════════════════════════════════════════════════

    async def generate_implant(self, config: dict) -> bytes:
        """Generate an implant with the given configuration"""
        try:
            # Build implant config based on type
            if config.get("beacon", False):
                implant = await self._client.generate_beacon(**self._build_implant_config(config))
            else:
                implant = await self._client.generate(**self._build_implant_config(config))

            return implant.File.Data
        except Exception as e:
            raise SliverCommandError(f"Failed to generate implant: {str(e)}")

    def _build_implant_config(self, config: dict) -> dict:
        """Build implant config for SliverPy"""
        # Map our config to SliverPy format
        c2_configs = []
        for c2 in config.get("c2", []):
            c2_configs.append(f"{c2['protocol']}://{c2['host']}:{c2['port']}")

        return {
            "name": config.get("name", ""),
            "os": config.get("os", "windows"),
            "arch": config.get("arch", "amd64"),
            "format": config.get("format", "exe"),
            "c2": c2_configs,
            "debug": config.get("debug", False),
            "evasion": config.get("evasion", True),
            # Beacon-specific
            "interval": config.get("interval", 60),
            "jitter": config.get("jitter", 30),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Helper Methods
    # ═══════════════════════════════════════════════════════════════════════════

    def _session_to_dict(self, session: Any) -> dict:
        """Convert Sliver session object to dict"""
        return {
            "id": session.ID,
            "name": session.Name,
            "hostname": session.Hostname,
            "username": session.Username,
            "uid": session.UID,
            "gid": session.GID,
            "os": session.OS,
            "arch": session.Arch,
            "transport": session.Transport,
            "remote_address": session.RemoteAddress,
            "pid": session.PID,
            "filename": session.Filename,
            "last_checkin": session.LastCheckin,
            "active_c2": session.ActiveC2,
            "reconnect_interval": session.ReconnectInterval,
            "proxy_url": session.ProxyURL,
        }

    def _beacon_to_dict(self, beacon: Any) -> dict:
        """Convert Sliver beacon object to dict"""
        return {
            "id": beacon.ID,
            "name": beacon.Name,
            "hostname": beacon.Hostname,
            "username": beacon.Username,
            "uid": beacon.UID,
            "gid": beacon.GID,
            "os": beacon.OS,
            "arch": beacon.Arch,
            "transport": beacon.Transport,
            "remote_address": beacon.RemoteAddress,
            "pid": beacon.PID,
            "filename": beacon.Filename,
            "last_checkin": beacon.LastCheckin,
            "next_checkin": beacon.NextCheckin,
            "interval": beacon.Interval,
            "jitter": beacon.Jitter,
            "active_c2": beacon.ActiveC2,
        }

    def _job_to_dict(self, job: Any) -> dict:
        """Convert Sliver job object to dict"""
        return {
            "id": str(job.ID),
            "name": job.Name,
            "protocol": job.Protocol if hasattr(job, "Protocol") else "unknown",
            "host": job.Host if hasattr(job, "Host") else "",
            "port": job.Port if hasattr(job, "Port") else 0,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Armory Operations (Extensions) - Using sliver-client CLI as workaround
    # sliver-py doesn't support armory operations, so we use subprocess
    # ═══════════════════════════════════════════════════════════════════════════

    _sliver_client_configured = False
    _armory_cache = None
    _armory_cache_time = 0
    _armory_cache_ttl = 300  # Cache for 5 minutes

    def _setup_sliver_client_config(self) -> bool:
        """Setup sliver-client config from operator config file"""
        import subprocess

        # Only run once per process (use class variable via self.__class__)
        if self.__class__._sliver_client_configured:
            return True

        config_path = Path(settings.sliver_config) if settings.sliver_config else None
        if not config_path or not config_path.exists():
            logger.warning("No operator config found for sliver-client setup")
            return False

        # Check if config is already imported
        client_config_dir = Path.home() / ".sliver-client" / "configs"
        if client_config_dir.exists() and list(client_config_dir.glob("*.cfg")):
            self.__class__._sliver_client_configured = True
            return True

        # Import operator config using sliver-client import command
        try:
            result = subprocess.run(
                ['/usr/local/bin/sliver-client', 'import', str(config_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "HOME": str(Path.home())}
            )
            if result.returncode == 0:
                logger.info(f"Imported operator config: {result.stdout}")
                self.__class__._sliver_client_configured = True
                return True
            else:
                logger.error(f"Failed to import config: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"Error importing config: {e}")
            return False

    async def _run_sliver_client_command(self, command: str, timeout: int = 120) -> str:
        """Run a sliver-client command and return output"""
        import asyncio
        import tempfile

        # Ensure config is set up
        self._setup_sliver_client_config()

        # Create a temporary rc script with the command
        # sliver-client uses --rc to run commands from a file
        # Add 'exit' at the end to ensure the client closes after running the command
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sliver', delete=False) as f:
            f.write(f"{command}\n")
            f.write("exit\n")
            rc_file = f.name

        try:
            process = await asyncio.create_subprocess_exec(
                '/usr/local/bin/sliver-client',
                'console',
                '--rc', rc_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": str(Path.home())}
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            # Clean up rc file
            try:
                os.unlink(rc_file)
            except:
                pass

            output = stdout.decode().strip()
            error = stderr.decode().strip()

            # Check for errors in output
            if process.returncode != 0 and not output:
                raise SliverCommandError(f"sliver-client error: {error or 'Unknown error'}")

            # Return combined output (sliver often writes to both stdout and stderr)
            return output or error

        except asyncio.TimeoutError:
            try:
                os.unlink(rc_file)
            except:
                pass
            raise SliverCommandError(f"Command timed out after {timeout}s: {command}")
        except SliverCommandError:
            raise
        except Exception as e:
            try:
                os.unlink(rc_file)
            except:
                pass
            raise SliverCommandError(f"Failed to run sliver-client: {str(e)}")

    async def _get_installed_packages(self) -> set:
        """Get set of installed package names using aliases and extensions commands"""
        installed = set()

        try:
            # Get installed aliases
            aliases_output = await self._run_sliver_client_command("aliases", timeout=30)
            aliases_output = self._strip_ansi(aliases_output)
            for line in aliases_output.split('\n'):
                if '✅' in line or 'true' in line.lower():
                    parts = line.split()
                    if len(parts) >= 2:
                        installed.add(parts[0].lower())
                        installed.add(parts[1].lower())  # Also add command name
        except Exception as e:
            logger.debug(f"Failed to get aliases: {e}")

        try:
            # Get installed extensions
            ext_output = await self._run_sliver_client_command("extensions", timeout=30)
            ext_output = self._strip_ansi(ext_output)
            for line in ext_output.split('\n'):
                if '✅' in line or 'installed' in line.lower():
                    parts = line.split()
                    if parts:
                        installed.add(parts[0].lower())
        except Exception as e:
            logger.debug(f"Failed to get extensions: {e}")

        return installed

    async def get_armory(self, force_refresh: bool = False) -> List[dict]:
        """Get list of available armory packages using sliver-client CLI (cached)"""
        import time

        if not self.is_connected:
            return []

        # Check cache first
        now = time.time()
        if not force_refresh and self.__class__._armory_cache is not None:
            if now - self.__class__._armory_cache_time < self.__class__._armory_cache_ttl:
                logger.debug("Returning cached armory data")
                # Still update installed status from quick aliases/extensions check
                try:
                    installed = await self._get_installed_packages()
                    for pkg in self.__class__._armory_cache:
                        pkg['installed'] = pkg.get('name', '').lower() in installed or \
                                          pkg.get('command_name', '').lower() in installed
                except:
                    pass
                return self.__class__._armory_cache

        try:
            # Try using sliver-client CLI
            logger.info("Fetching armory data from sliver-client...")
            output = await self._run_sliver_client_command("armory", timeout=120)
            packages = self._parse_armory_output(output)

            # Get installed packages
            try:
                installed = await self._get_installed_packages()
                for pkg in packages:
                    pkg['installed'] = pkg.get('name', '').lower() in installed or \
                                      pkg.get('command_name', '').lower() in installed
            except Exception as e:
                logger.warning(f"Failed to check installed packages: {e}")

            # Update cache
            self.__class__._armory_cache = packages
            self.__class__._armory_cache_time = now
            logger.info(f"Cached {len(packages)} armory packages")

            return packages
        except Exception as e:
            logger.error(f"Failed to get armory via CLI: {e}")
            # Return cache if available, otherwise mock data
            if self.__class__._armory_cache is not None:
                return self.__class__._armory_cache
            return self._get_mock_armory()

    def _strip_ansi(self, text: str) -> str:
        """Strip ANSI escape codes from text"""
        import re
        # Remove ANSI escape sequences
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    def _parse_armory_output(self, output: str) -> List[dict]:
        """Parse armory command output into structured data"""
        import re

        # Strip ANSI escape codes first
        output = self._strip_ansi(output)

        packages = []
        lines = output.strip().split('\n')

        # Find the separator line to know where data starts
        in_table = False
        for line in lines:
            # Skip empty lines
            if not line.strip():
                continue

            # Check for separator line (=== or ---)
            if re.match(r'^[=\-─━]+\s*[=\-─━]*', line.strip()):
                in_table = True
                continue

            # Skip lines before the table
            if not in_table:
                continue

            # Parse data rows
            # Format: "Default   bof-roast   v0.0.2    Extension   Help text..."
            # Use regex to match: word, spaces, word, spaces, version, spaces, type, spaces, rest
            match = re.match(
                r'^(\S+)\s+(\S+)\s+(v?[\d\.]+\S*)\s+(\S+)(?:\s+(.*))?$',
                line.strip()
            )

            if match:
                armory_name = match.group(1)
                command_name = match.group(2)
                version = match.group(3)
                pkg_type = match.group(4)
                help_text = match.group(5).strip() if match.group(5) else ""

                # Skip if it looks like a header
                if armory_name.lower() in ['armory', 'name', 'package', 'packages']:
                    continue

                packages.append({
                    "name": command_name,
                    "command_name": command_name,
                    "version": version,
                    "installed": False,
                    "type": pkg_type.lower() if pkg_type else "alias",
                    "repo_url": f"https://github.com/sliverarmory/{command_name}",
                    "help": help_text,
                    "armory": armory_name,
                })

        logger.info(f"Parsed {len(packages)} packages from armory output")
        return packages if packages else self._get_mock_armory()

    def _get_mock_armory(self) -> List[dict]:
        """Return mock armory data for development/fallback"""
        return [
            {"name": "sharpersist", "command_name": "sharpersist", "version": "1.0.4", "installed": False, "type": "alias", "repo_url": "https://github.com/mandiant/SharPersist"},
            {"name": "nanodump", "command_name": "nanodump", "version": "1.0.2", "installed": False, "type": "alias", "repo_url": "https://github.com/fortra/nanodump"},
            {"name": "rubeus", "command_name": "rubeus", "version": "2.2.0", "installed": False, "type": "alias", "repo_url": "https://github.com/GhostPack/Rubeus"},
            {"name": "seatbelt", "command_name": "seatbelt", "version": "1.2.1", "installed": False, "type": "alias", "repo_url": "https://github.com/GhostPack/Seatbelt"},
            {"name": "sharphound", "command_name": "sharphound", "version": "1.1.0", "installed": False, "type": "alias", "repo_url": "https://github.com/BloodHoundAD/SharpHound"},
            {"name": "certify", "command_name": "certify", "version": "1.0.1", "installed": False, "type": "alias", "repo_url": "https://github.com/GhostPack/Certify"},
            {"name": "sharpwmi", "command_name": "sharpwmi", "version": "1.0.0", "installed": False, "type": "alias", "repo_url": "https://github.com/GhostPack/SharpWMI"},
            {"name": "sharpview", "command_name": "sharpview", "version": "1.0.0", "installed": False, "type": "alias", "repo_url": "https://github.com/tevora-threat/SharpView"},
        ]

    async def install_armory_package(self, package_name: str) -> dict:
        """Install an armory package using sliver-client CLI"""
        # Validate package name to prevent command injection
        if not package_name.replace('-', '').replace('_', '').isalnum():
            raise SliverCommandError(f"Invalid package name: {package_name}")

        try:
            output = await self._run_sliver_client_command(
                f"armory install {package_name}",
                timeout=300  # Installation can take a while
            )
            logger.info(f"Armory install output: {output}")

            # Invalidate cache after install
            self.__class__._armory_cache = None

            return {
                "success": True,
                "package": package_name,
                "message": f"Successfully installed {package_name}",
                "output": self._strip_ansi(output)
            }
        except SliverCommandError as e:
            raise SliverCommandError(f"Failed to install {package_name}: {str(e)}")

    async def uninstall_armory_package(self, package_name: str) -> dict:
        """Uninstall an armory package using sliver-client CLI"""
        # Validate package name to prevent command injection
        if not package_name.replace('-', '').replace('_', '').isalnum():
            raise SliverCommandError(f"Invalid package name: {package_name}")

        try:
            output = await self._run_sliver_client_command(f"armory remove {package_name}")
            logger.info(f"Armory uninstall output: {output}")

            # Invalidate cache after uninstall
            self.__class__._armory_cache = None

            return {
                "success": True,
                "package": package_name,
                "message": f"Successfully uninstalled {package_name}",
                "output": self._strip_ansi(output)
            }
        except SliverCommandError as e:
            raise SliverCommandError(f"Failed to uninstall {package_name}: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Execute-Assembly Operations
    # ═══════════════════════════════════════════════════════════════════════════

    async def session_execute_assembly(
        self, session_id: str, assembly_path: str, arguments: str = "", timeout: int = 300
    ) -> dict:
        """Execute .NET assembly on session"""
        try:
            session = await self._client.interact_session(session_id)
            # Read assembly from local path
            with open(assembly_path, 'rb') as f:
                assembly_data = f.read()

            result = await asyncio.wait_for(
                session.execute_assembly(assembly_data, arguments),
                timeout=timeout
            )
            return {
                "output": result.Output.decode() if hasattr(result, 'Output') else "",
                "error": result.Error if hasattr(result, 'Error') else "",
            }
        except asyncio.TimeoutError:
            raise SliverCommandError(f"Execute-assembly timed out after {timeout}s")
        except FileNotFoundError:
            raise SliverCommandError(f"Assembly file not found: {assembly_path}")
        except Exception as e:
            raise SliverCommandError(f"Execute-assembly failed: {str(e)}")

    async def beacon_execute_assembly(
        self, beacon_id: str, assembly_path: str, arguments: str = ""
    ) -> dict:
        """Queue execute-assembly task on beacon"""
        try:
            beacon = await self._client.interact_beacon(beacon_id)
            # Read assembly from local path
            with open(assembly_path, 'rb') as f:
                assembly_data = f.read()

            task = await beacon.execute_assembly(assembly_data, arguments)
            return {
                "task_id": task.TaskID,
                "beacon_id": beacon_id,
                "assembly": assembly_path,
                "arguments": arguments,
            }
        except FileNotFoundError:
            raise SliverCommandError(f"Assembly file not found: {assembly_path}")
        except Exception as e:
            raise SliverCommandError(f"Failed to queue execute-assembly task: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Cleanup Operations
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_stale_sessions(self, threshold_minutes: int = 1440) -> List[dict]:
        """Get sessions that haven't checked in for a while"""
        from datetime import datetime, timedelta

        sessions = await self.get_sessions()
        stale = []
        threshold = datetime.utcnow() - timedelta(minutes=threshold_minutes)

        for session in sessions:
            last_checkin = session.get("last_checkin")
            if last_checkin:
                try:
                    # Parse timestamp (handle various formats)
                    if isinstance(last_checkin, str):
                        checkin_time = datetime.fromisoformat(last_checkin.replace('Z', '+00:00'))
                    else:
                        checkin_time = datetime.fromtimestamp(last_checkin)

                    if checkin_time.replace(tzinfo=None) < threshold:
                        session["stale_minutes"] = int((datetime.utcnow() - checkin_time.replace(tzinfo=None)).total_seconds() / 60)
                        stale.append(session)
                except:
                    pass

        return stale

    async def get_dead_beacons(self, missed_checkins: int = 10) -> List[dict]:
        """Get beacons that have missed multiple check-ins"""
        from datetime import datetime

        beacons = await self.get_beacons()
        dead = []

        for beacon in beacons:
            interval = beacon.get("interval", 60)
            last_checkin = beacon.get("last_checkin")

            if last_checkin:
                try:
                    if isinstance(last_checkin, str):
                        checkin_time = datetime.fromisoformat(last_checkin.replace('Z', '+00:00'))
                    else:
                        checkin_time = datetime.fromtimestamp(last_checkin)

                    seconds_since = (datetime.utcnow() - checkin_time.replace(tzinfo=None)).total_seconds()
                    expected_checkins = seconds_since / interval

                    if expected_checkins >= missed_checkins:
                        beacon["missed_checkins"] = int(expected_checkins)
                        dead.append(beacon)
                except:
                    pass

        return dead

    async def bulk_kill_sessions(self, session_ids: List[str]) -> dict:
        """Kill multiple sessions at once"""
        results = {"success": [], "failed": []}

        for session_id in session_ids:
            try:
                await self.kill_session(session_id)
                results["success"].append(session_id)
            except Exception as e:
                results["failed"].append({"id": session_id, "error": str(e)})

        return results

    async def bulk_kill_beacons(self, beacon_ids: List[str]) -> dict:
        """Kill multiple beacons at once"""
        results = {"success": [], "failed": []}

        for beacon_id in beacon_ids:
            try:
                await self.kill_beacon(beacon_id)
                results["success"].append(beacon_id)
            except Exception as e:
                results["failed"].append({"id": beacon_id, "error": str(e)})

        return results

    async def kill_all_sessions(self) -> dict:
        """Kill all sessions"""
        sessions = await self.get_sessions()
        session_ids = [s["id"] for s in sessions]
        return await self.bulk_kill_sessions(session_ids)

    async def kill_all_beacons(self) -> dict:
        """Kill all beacons"""
        beacons = await self.get_beacons()
        beacon_ids = [b["id"] for b in beacons]
        return await self.bulk_kill_beacons(beacon_ids)

    async def kill_all_jobs(self) -> dict:
        """Kill all jobs/listeners"""
        jobs = await self.get_jobs()
        results = {"success": [], "failed": []}

        for job in jobs:
            try:
                await self.kill_job(int(job["id"]))
                results["success"].append(job["id"])
            except Exception as e:
                results["failed"].append({"id": job["id"], "error": str(e)})

        return results


# Global Sliver manager instance
sliver_manager = SliverManager()


async def get_sliver_client() -> SliverManager:
    """Dependency to get Sliver client"""
    if not sliver_manager.is_connected:
        try:
            await sliver_manager.connect()
        except SliverConnectionError:
            pass  # Allow operation without Sliver for development
    return sliver_manager
