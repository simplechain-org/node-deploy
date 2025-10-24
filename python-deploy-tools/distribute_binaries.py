#!/usr/bin/env python3
"""
BSC Node Binary Distribution Script
Distributes BSC node binaries to remote servers
"""

import os
import yaml
import paramiko
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BinaryDistributor:
    def __init__(self, config_path: str):
        self.config = self.load_config(config_path)
        self.binaries_dir = os.path.join(Path(__file__).resolve().parent, "bin")

    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Load deployment configuration from YAML file"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def create_ssh_client(self, server_config: Dict[str, Any]) -> paramiko.SSHClient:
        """Create SSH client for server connection"""
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Expand user home directory in ssh_key path
        ssh_key_path = os.path.expanduser(server_config['ssh_key'])
        
        try:
            ssh_client.connect(
                hostname=server_config['host'],
                username=server_config['user'],
                key_filename=ssh_key_path,
                timeout=30
            )
            return ssh_client
        except Exception as e:
            logger.error(f"Failed to connect to {server_config['host']}: {e}")
            raise

    def ensure_remote_directory(self, ssh_client: paramiko.SSHClient, remote_dir: str):
        """Ensure remote directory exists"""
        try:
            stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {remote_dir}")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error = stderr.read().decode()
                raise Exception(f"Failed to create directory {remote_dir}: {error}")
        except Exception as e:
            logger.error(f"Error creating remote directory {remote_dir}: {e}")
            raise

    def distribute_binary_to_server(self, server_config: Dict[str, Any]) -> bool:
        """Distribute binary files to a specific server"""
        server_name = server_config['name']
        logger.info(f"Distributing binaries to {server_name}")

        try:
            # Create SSH client
            ssh_client = self.create_ssh_client(server_config)

            # Create remote directories
            current_dir = Path(__file__).resolve().parent
            remote_base = f"{current_dir}/sipc2/{server_name}"
            self.ensure_remote_directory(ssh_client, remote_base)
            self.ensure_remote_directory(ssh_client, f"{remote_base}/bin")

            # Distribute binary files
            with ssh_client.open_sftp() as sftp:
                # List all files in the local bin directory
                if not os.path.exists(self.binaries_dir):
                    logger.error(f"Binaries directory not found: {self.binaries_dir}")
                    return False

                binary_files = os.listdir(self.binaries_dir)
                if not binary_files:
                    logger.warning(f"No binary files found in {self.binaries_dir}")
                    return False

                for filename in binary_files:
                    local_path = os.path.join(self.binaries_dir, filename)
                    remote_path = f"{remote_base}/bin/{filename}"
                    
                    # Upload file
                    sftp.put(local_path, remote_path)
                    # Make binary executable
                    ssh_client.exec_command(f"chmod +x {remote_path}")
                    logger.info(f"Uploaded {filename} to {server_name}:{remote_path}")

            ssh_client.close()
            logger.info(f"Successfully distributed binaries to {server_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to distribute binaries to {server_name}: {e}")
            return False

    def distribute_binaries(self, server_names: List[str] = None, parallel: bool = True, max_parallel: int = 5) -> bool:
        """Distribute binary files to servers"""
        logger.info("Starting binary distribution")

        # Check if binaries directory exists
        if not os.path.exists(self.binaries_dir):
            logger.error(f"Binaries directory not found: {self.binaries_dir}")
            return False

        # Get list of servers to distribute to
        servers = self.config['servers']
        if server_names:
            servers = [s for s in servers if s['name'] in server_names]
            if not servers:
                logger.error(f"No matching servers found for: {server_names}")
                return False

        if not servers:
            logger.error("No servers configured")
            return False

        logger.info(f"Distributing binaries to {len(servers)} servers")

        # Distribute binaries
        if parallel and len(servers) > 1:
            logger.info(f"Starting parallel distribution to {len(servers)} servers")

            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                futures = {
                    executor.submit(self.distribute_binary_to_server, server): server
                    for server in servers
                }

                success_count = 0
                for future in as_completed(futures):
                    server = futures[future]
                    try:
                        if future.result():
                            success_count += 1
                    except Exception as e:
                        logger.error(f"Distribution to {server['name']} failed: {e}")

                logger.info(f"Binary distribution completed: {success_count}/{len(servers)} successful")
                return success_count == len(servers)
        else:
            logger.info("Starting sequential distribution")

            success_count = 0
            for server in servers:
                if self.distribute_binary_to_server(server):
                    success_count += 1

            logger.info(f"Binary distribution completed: {success_count}/{len(servers)} successful")
            return success_count == len(servers)

    def list_binaries(self) -> List[str]:
        """List all binary files in the binaries directory"""
        if not os.path.exists(self.binaries_dir):
            return []
        
        return os.listdir(self.binaries_dir)


def main():
    parser = argparse.ArgumentParser(description="BSC Node Binary Distributor")
    parser.add_argument("--config", default="deployment-config.yaml", help="Path to deployment config file")
    parser.add_argument("--servers", nargs='+', help="Specific server names to distribute to (default: all)")
    parser.add_argument("--list", action="store_true", help="List available binary files")
    parser.add_argument("--sequential", action="store_true", help="Run distribution sequentially instead of parallel")
    parser.add_argument("--max-parallel", type=int, default=5, help="Maximum number of parallel distributions")

    args = parser.parse_args()

    # Validate config file
    if not os.path.exists(args.config):
        logger.error(f"Configuration file not found: {args.config}")
        return 1

    try:
        distributor = BinaryDistributor(args.config)

        if args.list:
            binaries = distributor.list_binaries()
            if binaries:
                print("Available binary files:")
                for binary in binaries:
                    print(f"  - {binary}")
            else:
                print("No binary files found in bin directory")
            return 0

        # Distribute binaries
        success = distributor.distribute_binaries(
            server_names=args.servers,
            parallel=not args.sequential,
            max_parallel=args.max_parallel
        )

        return 0 if success else 1

    except Exception as e:
        logger.error(f"Binary distribution failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())