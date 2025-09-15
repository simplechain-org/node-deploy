#!/usr/bin/env python3
"""
BSC Cluster Deployment Script (Native Version)
Deploys BSC nodes to multiple servers using native binaries instead of Docker
"""

import os
import yaml
import paramiko
import argparse
import subprocess
import time
import secrets
import json
from pathlib import Path
from typing import Dict, List, Any
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from file_distributor import FileDistributor
from eth_account import Account

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BSCNativeClusterDeployer:
    def __init__(self, config_path: str):
        self.config = self.load_config(config_path)
        self.distributor = FileDistributor(config_path)

    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Load deployment configuration from YAML file"""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _ensure_dir(self, path: str):
        os.makedirs(path, exist_ok=True)

    def _write_text_file(self, path: str, content: str):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _generate_password(self) -> str:
        return secrets.token_urlsafe(24)

    def _ensure_password_file(self, keys_base_dir: str) -> str:
        password_path = os.path.join(keys_base_dir, 'password.txt')
        if not os.path.exists(password_path):
            password = self._generate_password()
            self._write_text_file(password_path, password)
            logger.info(f"Generated new password file: {password_path}")
        return password_path

    def _generate_nodekey(self, target_path: str):
        if os.path.exists(target_path):
            logger.info(f"Nodekey already exists: {target_path}")
            return
        key_bytes = secrets.token_bytes(32)
        key_hex = key_bytes.hex()
        self._write_text_file(target_path, key_hex)
        logger.info(f"Generated nodekey: {target_path}")

    def _generate_eth_keystore(self, out_dir: str, password: str) -> str:
        """Generate an Ethereum account keystore JSON in out_dir; returns address."""
        self._ensure_dir(out_dir)
        acct = Account.create()
        keystore = Account.encrypt(acct.key, password)
        filename = f"UTC--{time.strftime('%Y-%m-%dT%H-%M-%SZ', time.gmtime())}--{acct.address.lower().replace('0x','')}"
        keystore_path = os.path.join(out_dir, filename)
        with open(keystore_path, 'w', encoding='utf-8') as f:
            json.dump(keystore, f)
        logger.info(f"Generated keystore: {keystore_path}")
        return acct.address

    def generate_keys_for_server(self, server_config: Dict[str, Any]) -> bool:
        """Generate required keys locally for a server if missing."""
        try:
            current_dir = Path(__file__).resolve().parent
            keys_base = os.path.join(current_dir, self.config['files']['keys_base'])
            node_index = server_config['node_index']
            role = server_config['role']

            # Ensure base dir
            self._ensure_dir(keys_base)

            # Ensure password file
            password_path = self._ensure_password_file(keys_base)
            with open(password_path, 'r', encoding='utf-8') as f:
                password = f.read().strip()

            # Generate role-specific nodekey
            nodekey_filename = None
            if role == 'validator':
                nodekey_filename = f"validator-nodekey{node_index}"
            elif role == 'sentry':
                nodekey_filename = f"sentry-nodekey{node_index}"
            elif role == 'fullnode':
                nodekey_filename = f"fullnode-nodekey{node_index}"
            else:
                logger.warning(f"Unknown role {role}; skipping nodekey generation")

            if nodekey_filename:
                self._generate_nodekey(os.path.join(keys_base, nodekey_filename))

            if role == 'validator':
                # Operator and consensus keystores
                operator_keystore_dir = os.path.join(keys_base, f"validator{node_index}", "keystore")
                consensus_keystore_dir = os.path.join(keys_base, f"consensus{node_index}", "keystore")
                if not os.path.isdir(operator_keystore_dir) or not os.listdir(operator_keystore_dir):
                    self._generate_eth_keystore(operator_keystore_dir, password)
                else:
                    logger.info(f"Operator keystore exists: {operator_keystore_dir}")
                if not os.path.isdir(consensus_keystore_dir) or not os.listdir(consensus_keystore_dir):
                    self._generate_eth_keystore(consensus_keystore_dir, password)
                else:
                    logger.info(f"Consensus keystore exists: {consensus_keystore_dir}")

                # Ensure BLS directory (place wallet files if required externally)
                bls_dir = os.path.join(keys_base, f"bls{node_index}")
                self._ensure_dir(bls_dir)

            return True
        except Exception as e:
            logger.error(f"Failed to generate keys for server {server_config['name']}: {e}")
            return False

    def add_node(self, server_name: str, update_peers_config: bool = True) -> bool:
        """Add a new node: generate keys, distribute files/config, and start the node."""
        try:
            servers = self.config['servers']
            target = None
            for s in servers:
                if s['name'] == server_name:
                    target = s
                    break
            if target is None:
                logger.error(f"Server not found in config: {server_name}")
                return False

            # 1) Generate keys for the new server
            if not self.generate_keys_for_server(target):
                return False

            # 2) Distribute files: update configs for all servers if requested, else only target
            if update_peers_config:
                logger.info("Updating configs for all servers to include new static/trusted nodes...")
                for s in servers:
                    if not self.distributor.distribute_files_to_server(s):
                        logger.warning(f"Failed distributing to {s['name']}")
            else:
                if not self.distributor.distribute_files_to_server(target):
                    return False

            # 3) Deploy the new server
            return self.deploy_to_server(target)
        except Exception as e:
            logger.error(f"Add node failed: {e}")
            return False

    def create_ssh_client(self, server_config: Dict[str, Any]) -> paramiko.SSHClient:
        """Create SSH client for server connection"""
        return self.distributor.create_ssh_client(server_config)

    def build_geth_binary(self) -> bool:
        """Build Geth binary locally"""
        logger.info("Building Geth binary...")

        try:
            # Check if bsc directory exists
            if not os.path.exists("bsc"):
                logger.info("Cloning BSC repository...")
                cmd = "git clone https://github.com/bnb-chain/bsc.git"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"Failed to clone BSC repository: {result.stderr}")
                    return False

            # Build geth binary
            cmd = "cd bsc && make geth"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if result.returncode != 0:
                logger.error(f"Geth build failed: {result.stderr}")
                return False

            # Copy binary to bin directory
            os.makedirs("bin", exist_ok=True)
            cmd = "cp bsc/build/bin/geth bin/"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Failed to copy geth binary: {result.stderr}")
                return False

            logger.info("Geth binary built successfully")
            return True

        except Exception as e:
            logger.error(f"Error building Geth binary: {e}")
            return False

    def distribute_binaries(self) -> bool:
        """Distribute node binaries to all servers"""
        logger.info("Distributing node binaries to servers...")
        
        try:
            # Check if distribute_binaries.py exists
            current_dir = Path(__file__).resolve().parent
            distrib_script = os.path.join(current_dir, "distribute_binaries.py")
            
            if not os.path.exists(distrib_script):
                logger.error(f"Binary distribution script not found: {distrib_script}")
                return False
            
            # Run the binary distribution script
            cmd = ["python3", distrib_script, "--config", "deployment-config.yaml"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Binary distribution failed: {result.stderr}")
                return False
                
            logger.info("Node binaries distributed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error during binary distribution: {e}")
            return False

    def deploy_to_server(self, server_config: Dict[str, Any]) -> bool:
        """Deploy BSC node to a specific server using native binary"""
        server_name = server_config['name']
        role = server_config['role']
        ports = server_config['ports']
        node_index = server_config['node_index']

        logger.info(f"Starting native deployment to {server_name}")
        current_dir = Path(__file__).resolve().parent

        # Get validator address for validator nodes
        validator_address = "0x0000000000000000000000000000000000000000"
        if role == 'validator':
            validator_address = self.get_validator_address(node_index)
            logger.info(f"Using validator address: {validator_address}")

        try:
            # Create SSH client
            ssh_client = self.create_ssh_client(server_config)

            # Create remote directories (per-node isolation)
            remote_base = f"{current_dir}/sipc2/{server_name}"
            self.distributor.ensure_remote_directory(ssh_client, remote_base)
            self.distributor.ensure_remote_directory(ssh_client, f"{remote_base}/data")
            self.distributor.ensure_remote_directory(ssh_client, f"{remote_base}/keys")
            self.distributor.ensure_remote_directory(ssh_client, f"{remote_base}/config")

            # Initialize the node with genesis file before starting
            init_cmd = self.generate_init_command(server_config)
            logger.info(f"Initialization command: {init_cmd}")

            # Generate startup command
            startup_cmd = self.generate_native_run_command(server_config, validator_address)
            logger.info(f"Startup command: {startup_cmd}")

            # Create deployment script
            if role == 'validator':
                deployment_script = f"""#!/bin/bash
set -e

echo "Starting BSC validator node deployment on {server_name}"

# Kill existing process if running
pkill -f "geth.*{server_name}" 2>/dev/null || true

# Clean up mounted data directory to prevent dirty data issues
echo "Cleaning up data directory to prevent dirty data..."
if [ -d "{remote_base}/data" ]; then
    rm -rf {remote_base}/data/*
    find {remote_base}/data -name ".*" -type f -delete 2>/dev/null || true
fi

# Initialize node with genesis file
echo "Initializing node with genesis file..."
{init_cmd}

# Run Geth binary
{startup_cmd}

echo "BSC validator node deployment completed on {server_name}"

# Wait for node to be ready for staking
echo "Waiting for node to be ready for staking..."
sleep 45

echo "Validator registration and staking completed"
"""
            else:
                # Check if we should clean data directory
                clean_data = self.config['options'].get('clean_before_deploy', True)

                deployment_script = f"""#!/bin/bash
set -e

echo "Starting BSC {role} node deployment on {server_name}"

# Kill existing process if running
pkill -f "geth.*{server_name}" 2>/dev/null || true

# Clean up mounted data directory to prevent dirty data issues
if [ "{clean_data}" = "True" ]; then
    echo "Cleaning up data directory to prevent dirty data..."
    if [ -d "{remote_base}/data" ]; then
        rm -rf {remote_base}/data/*
        find {remote_base}/data -name ".*" -type f -delete 2>/dev/null || true
    fi
else
    echo "Skipping data directory cleanup (clean_before_deploy=False)"
fi

# Initialize node with genesis file
echo "Initializing node with genesis file..."
{init_cmd}

# Run Geth binary
{startup_cmd}

echo "BSC {role} node deployment completed on {server_name}"
"""

            # Upload and execute deployment script
            remote_script_path = f"{remote_base}/deploy.sh"
            with ssh_client.open_sftp() as sftp:
                with sftp.file(remote_script_path, 'w') as f:
                    f.write(deployment_script)

            # Make script executable and run it
            stdin, stdout, stderr = ssh_client.exec_command(f"chmod +x {remote_script_path} && {remote_script_path}")
            exit_code = stdout.channel.recv_exit_status()

            if exit_code != 0:
                error = stderr.read().decode()
                logger.error(f"Deployment failed on {server_name}: {error}")
                ssh_client.close()
                return False

            logger.info(f"Deployment completed successfully on {server_name}")
            ssh_client.close()
            return True

        except Exception as e:
            logger.error(f"Deployment failed on {server_name}: {e}")
            return False

    def generate_init_command(self, server_config: Dict[str, Any]) -> str:
        """Generate geth init command for server"""
        server_name = server_config['name']
        node_index = server_config['node_index']
        role = server_config['role']

        # Generate paths
        current_dir = Path(__file__).resolve().parent
        remote_base = f"{current_dir}/sipc2/{server_name}"

        # Build geth init command
        init_cmd = [
            f"{remote_base}/bin/geth",
            f"--datadir {remote_base}/data",
            "init",
            "--state.scheme path",
            "--db.engine pebble",
            f"{remote_base}/config/genesis.json"
        ]

        # For first validator node, use different parameters
        if role == 'validator' and node_index == 0:
            init_cmd[3] = "--state.scheme hash"
            init_cmd[4] = "--db.engine leveldb"

        # For second validator node, use multidatabase
        if role == 'validator' and node_index == 1:
            init_cmd.insert(5, "--multidatabase")

        return " ".join(init_cmd)

    def generate_native_run_command(self, server_config: Dict[str, Any], validator_address: str = None) -> str:
        """Generate native geth run command for server"""
        container_name = server_config['name']
        node_index = server_config['node_index']
        role = server_config['role']

        # Generate port mappings
        ports = server_config['ports']
        
        # Generate paths (match docker-entrypoint.sh paths)
        current_dir = Path(__file__).resolve().parent
        remote_base = f"{current_dir}/sipc2/{container_name}"

        # Build native geth run command with proper port mappings
        if role == 'validator':
            # Use provided validator address or default
            if validator_address is None:
                validator_address = "0x0000000000000000000000000000000000000000"

            # Validator node startup command with proper port mappings
            geth_cmd = [
                f"{remote_base}/bin/geth",
                f"--config {remote_base}/config/config.toml",
                "--mine --vote",
                f"--password {remote_base}/keys/password.txt",
                f"--unlock {validator_address}",
                f"--miner.etherbase {validator_address}",
                f"--blspassword {remote_base}/keys/password.txt",
                f"--nodekey {remote_base}/keys/validator-nodekey",
                f"--blswallet {remote_base}/keys/bls/wallet",
                f"--keystore {remote_base}/keys/consensus/keystore",
                "--rpc.allow-unprotected-txs --allow-insecure-unlock",
                f"--ws.addr 0.0.0.0 --ws.port {ports['ws']}",
                f"--http.addr 0.0.0.0 --http.port {ports['http']} --http.corsdomain '*'",
                f"--metrics --metrics.addr localhost --metrics.port {ports['metrics']} --metrics.expensive",
                f"--port {ports['p2p']}",  # 添加P2P端口配置
                "--pprof --pprof.addr localhost --pprof.port 6060",
                "--gcmode full --syncmode full --monitor.maliciousvote",
                "--override.passedforktime 1725500000 --override.lorentz 1725500000 --override.maxwell 1725500000",
                "--override.immutabilitythreshold 100 --override.breatheblockinterval 600",
                "--override.minforblobrequest 20 --override.defaultextrareserve 10",
                f"--datadir {remote_base}/data"
            ]
        else:
            # Sentry/Full node startup command with proper port mappings
            geth_cmd = [
                f"{remote_base}/bin/geth",
                f"--config {remote_base}/config/config.toml",
                "--rpc.allow-unprotected-txs --allow-insecure-unlock",
                f"--ws.addr 0.0.0.0 --ws.port {ports['ws']}",
                f"--http.addr 0.0.0.0 --http.port {ports['http']} --http.corsdomain '*'",
                f"--metrics --metrics.addr localhost --metrics.port {ports['metrics']} --metrics.expensive",
                f"--port {ports['p2p']}",  # 添加P2P端口配置
                "--pprof --pprof.addr localhost --pprof.port 6060",
                "--gcmode full --syncmode full",
                "--override.passedforktime 1725500000 --override.lorentz 1725500000 --override.maxwell 1725500000",
                "--override.immutabilitythreshold 100 --override.breatheblockinterval 600",
                "--override.minforblobrequest 20 --override.defaultextrareserve 10",
                f"--datadir {remote_base}/data"
            ]

        return " ".join(geth_cmd) + " &"

    def get_validator_address(self, node_index: int) -> str:
        """Get validator address from keystore file"""
        import os
        import json

        try:
            # Path to validator keystore directory
            current_dir = Path(__file__).resolve().parent
            validator_dir = os.path.join(current_dir, f"keys/consensus{node_index}/keystore")

            if not os.path.exists(validator_dir):
                logger.error(f"Validator keystore directory not found: {validator_dir}")
                return "0x0000000000000000000000000000000000000000"

            # Find keystore file (should be the only file in the directory)
            keystore_files = [f for f in os.listdir(validator_dir)]
            if not keystore_files:
                logger.error(f"No keystore file found in {validator_dir}")
                return "0x0000000000000000000000000000000000000000"

            keystore_file = os.path.join(validator_dir, keystore_files[0])

            # Read and parse keystore file
            with open(keystore_file, 'r') as f:
                keystore_data = json.load(f)

            address = keystore_data.get('address', '')
            if address:
                # Ensure address has 0x prefix
                if not address.startswith('0x'):
                    address = f"0x{address}"
                logger.info(f"Found validator address for node {node_index}: {address}")
                return address
            else:
                logger.error(f"No address found in keystore file {keystore_file}")
                return "0x0000000000000000000000000000000000000000"

        except Exception as e:
            logger.error(f"Error reading validator address for node {node_index}: {e}")
            return "0x0000000000000000000000000000000000000000"

    def regenerate_genesis_and_configs(self) -> bool:
        """Regenerate genesis.json and config files if configured"""
        logger.info("Checking if genesis and config regeneration is needed...")

        regenerate_genesis = self.config['options'].get('regenerate_genesis', False)
        regenerate_configs = self.config['options'].get('regenerate_configs', False)

        if not regenerate_genesis and not regenerate_configs:
            logger.info("Genesis and config regeneration are both disabled, skipping...")
            return True

        try:
            # Check if BSC_CLUSTER_SIZE in .env matches cluster.size in deployment config
            cluster_size = self.config['cluster']['size']
            env_file_path = ".env"
            
            if os.path.exists(env_file_path):
                # Read .env file
                with open(env_file_path, 'r') as f:
                    env_content = f.read()
                
                # Extract BSC_CLUSTER_SIZE from .env
                import re
                match = re.search(r'BSC_CLUSTER_SIZE=(\d+)', env_content)
                if match:
                    env_cluster_size = int(match.group(1))
                    
                    # If they don't match, update .env file
                    if env_cluster_size != cluster_size:
                        logger.info(f"Updating BSC_CLUSTER_SIZE in .env from {env_cluster_size} to {cluster_size}")
                        # Replace the BSC_CLUSTER_SIZE line
                        env_content = re.sub(
                            r'BSC_CLUSTER_SIZE=\d+', 
                            f'BSC_CLUSTER_SIZE={cluster_size}', 
                            env_content
                        )
                        
                        # Write back to .env file
                        with open(env_file_path, 'w') as f:
                            f.write(env_content)
                        logger.info(".env file updated successfully")
                else:
                    logger.warning("BSC_CLUSTER_SIZE not found in .env file")
            else:
                logger.warning(".env file not found")

            if regenerate_genesis:
                logger.info("Regenerating genesis.json and base config...")

                # Call bsc_cluster.sh to regenerate genesis
                import subprocess
                script_path = "./bsc_cluster.sh"
                if not os.path.exists(script_path):
                    print(f"ERROR: Script not found at {script_path}")
                else:
                    result = subprocess.run(["bash", script_path, "regen-genesis"], 
                                        capture_output=True, 
                                        text=True, 
                                        cwd=".")
                    
                    print("Return code:", result.returncode)
                    print("STDOUT:", result.stdout)
                    if result.stderr:
                        print("STDERR:", result.stderr)
                    if result.returncode != 0:
                        logger.error(f"Failed to regenerate genesis: {result.stderr}")
                        return False
                    logger.info("Genesis and config regeneration completed successfully")

            if regenerate_configs:
                logger.info("Server-specific config regeneration will be handled during file distribution")

            return True

        except Exception as e:
            logger.error(f"Error during regeneration: {e}")
            return False

    def monitor_deployment(self) -> Dict[str, Any]:
        """Monitor deployment status"""
        logger.info("Monitoring deployment status...")

        status = {}
        servers = self.config['servers']

        for server in servers:
            server_name = server['name']
            status[server_name] = self.check_server_status(server)   

        return status

    def check_server_status(self, server_config: Dict[str, Any]) -> Dict[str, str]:
        """Check status of BSC node on server"""
        try:
            ssh_client = self.create_ssh_client(server_config)
            server_name = server_config['name']

            # Check if process is running
            stdin, stdout, stderr = ssh_client.exec_command(f"pgrep -f 'geth.*{server_name}'")
            pid_output = stdout.read().decode().strip()

            if pid_output:
                return {
                    "status": "running",
                    "health": "unknown",
                    "details": f"Process running with PID(s): {pid_output}"
                }
            else:
                return {
                    "status": "stopped",
                    "health": "n/a",
                    "details": "Process not running"
                }

        except Exception as e:
            return {
                "status": "error",
                "health": "n/a",
                "details": str(e)
            }

    def deploy_cluster(self) -> bool:
        """Deploy BSC cluster to all servers using native binaries"""
        logger.info("Starting BSC cluster deployment (native)")

        # Build Geth binary
        if not self.config['options'].get('skip_build', False):
            if not self.build_geth_binary():
                return False

        # Regenerate genesis and configs if needed
        if not self.regenerate_genesis_and_configs():
            logger.error("Failed to regenerate genesis and configs")
            return False

        # Distribute binaries to all servers
        if not self.config['options'].get('skip_binary_distribution', False):
            if not self.distribute_binaries():
                logger.error("Failed to distribute node binaries")
                return False

        # Distribute files
        if not self.config['options'].get('skip_distribution', False):
            if not self.distributor.distribute_files(
                parallel=self.config['options'].get('parallel_deployment', True),
                max_parallel=self.config['options'].get('max_parallel', 5)
            ):
                return False

        # Deploy to servers
        servers = self.config['servers']
        parallel = self.config['options'].get('parallel_deployment', True)
        max_parallel = self.config['options'].get('max_parallel', 5)

        if parallel and len(servers) > 1:
            logger.info(f"Starting parallel deployment to {len(servers)} servers")

            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                futures = [
                    executor.submit(self.deploy_to_server, server)
                    for server in servers
                ]

                success_count = 0
                for future in as_completed(futures):
                    if future.result():
                        success_count += 1

                logger.info(f"Deployment completed: {success_count}/{len(servers)} successful")
                return success_count == len(servers)
        else:
            logger.info("Starting sequential deployment")

            success_count = 0
            for server in servers:
                if self.deploy_to_server(server):
                    success_count += 1

            logger.info(f"Deployment completed: {success_count}/{len(servers)} successful")
            return success_count == len(servers)


def main():
    parser = argparse.ArgumentParser(description="BSC Cluster Deployer (Native Version)")
    parser.add_argument("--config", default="deployment-config.yaml", help="Path to deployment config file")
    parser.add_argument("--action", choices=['deploy', 'monitor', 'files', 'add-node'], default='deploy', help="Action to perform")
    parser.add_argument("--server-name", help="Server name (from deployment-config.yaml) for add-node action")
    parser.add_argument("--no-update-peers-config", action="store_true", help="When adding node, do not refresh configs for all peers")
    parser.add_argument("--skip-build", action="store_true", help="Skip Geth binary build")
    parser.add_argument("--skip-binary-distribution", action="store_true", help="Skip node binary distribution")
    parser.add_argument("--skip-distribution", action="store_true", help="Skip file distribution")
    parser.add_argument("--regenerate-genesis", action="store_true", help="Force regenerate genesis.json")
    parser.add_argument("--no-regenerate-genesis", action="store_true", help="Skip genesis.json regeneration")
    parser.add_argument("--regenerate-configs", action="store_true", help="Force regenerate server-specific configs")
    parser.add_argument("--no-regenerate-configs", action="store_true", help="Skip server-specific config regeneration")

    args = parser.parse_args()

    # Validate config file
    if not os.path.exists(args.config):
        logger.error(f"Configuration file not found: {args.config}")
        return 1

    try:
        deployer = BSCNativeClusterDeployer(args.config)

        # Override config options with command line args
        if args.skip_build:
            deployer.config['options']['skip_build'] = True
        if args.skip_binary_distribution:
            deployer.config['options']['skip_binary_distribution'] = True
        if args.skip_distribution:
            deployer.config['options']['skip_distribution'] = True

        # Handle genesis regeneration options
        if args.regenerate_genesis:
            deployer.config['options']['regenerate_genesis'] = True
        if args.no_regenerate_genesis:
            deployer.config['options']['regenerate_genesis'] = False

        # Handle config regeneration options
        if args.regenerate_configs:
            deployer.config['options']['regenerate_configs'] = True
        if args.no_regenerate_configs:
            deployer.config['options']['regenerate_configs'] = False

        if args.action == 'deploy':
            success = deployer.deploy_cluster()
        elif args.action == 'monitor':
            status = deployer.monitor_deployment()
            print(yaml.dump(status, default_flow_style=False))
            success = True
        elif args.action == 'files':
            success = deployer.distributor.distribute_files()
        elif args.action == 'add-node':
            if not args.server_name:
                logger.error("--server-name is required for add-node action")
                return 1
            success = deployer.add_node(args.server_name, update_peers_config=not args.no_update_peers_config)

        return 0 if success else 1

    except Exception as e:
        logger.error(f"Deployment failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())