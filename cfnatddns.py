import socket
import threading
import time
import json
import select
import http.client
import dns.resolver
import logging
import os
from logging.handlers import RotatingFileHandler

# --- Configuration ---
CONFIG_FILE = "config.json"
LOCAL_PROXY_BIND_IP = "127.0.0.1" # Local IP for proxy to listen on
CLOUDFLARE_CDN_PORT = 443 # Common HTTPS port for Cloudflare CDN
DNS_TIMEOUT = 5 # seconds for DNS resolution

# --- Logging Setup ---
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "cfnatddns.log")
MAX_LOG_SIZE = 5 * 1024 * 1024 # 5 MB
BACKUP_COUNT = 3 # Keep 3 backup log files

# Ensure log directory exists
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# Configure logger
logger = logging.getLogger('cfnatddns_logger')
logger.setLevel(logging.INFO) # Set minimum logging level

# File handler for persistent logs
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Console handler for real-time output during development (will be hidden with --noconsole)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(console_handler)

# --- Global variables and locks ---
_node_dns_ips = {} # Stores DNS resolution results for each node
_dns_lock = threading.Lock() # Thread-safe lock

# --- Helper function: DNS Query ---
def resolve_dns(hostname, record_type='A'):
    """
    Queries the specified hostname for A or AAAA records using dnspython.
    """
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = DNS_TIMEOUT
        resolver.lifetime = DNS_TIMEOUT
        answers = resolver.resolve(hostname, record_type)
        ips = [str(rdata) for rdata in answers]
        return ips
    except dns.resolver.NXDOMAIN:
        logger.warning(f"DNS query failed: {hostname} does not exist.")
    except dns.resolver.Timeout:
        logger.warning(f"DNS query timed out: {hostname}.")
    except Exception as e:
        logger.error(f"DNS query for {hostname} encountered an error: {e}")
    return []

def update_node_ips(node_name, record_name, enable_ipv4, enable_ipv6):
    """
    Queries and updates the best IP for the specified node.
    """
    global _node_dns_ips
    current_ipv4 = None
    current_ipv6 = None

    if enable_ipv4:
        ipv4_ips = resolve_dns(record_name, 'A')
        if ipv4_ips:
            current_ipv4 = ipv4_ips[0]
            # logger.info(f"[{node_name}] DNS resolved IPv4: {current_ipv4}") # Too chatty for logs
        else:
            logger.warning(f"[{node_name}] Failed to resolve IPv4 address for {record_name}.")

    if enable_ipv6:
        ipv6_ips = resolve_dns(record_name, 'AAAA')
        if ipv6_ips:
            current_ipv6 = ipv6_ips[0]
            # logger.info(f"[{node_name}] DNS resolved IPv6: {current_ipv6}") # Too chatty for logs
        else:
            logger.warning(f"[{node_name}] Failed to resolve IPv6 address for {record_name}.")

    with _dns_lock:
        _node_dns_ips[node_name] = {"ipv4": current_ipv4, "ipv6": current_ipv6}

# --- DNSUpdateMonitor Class: Periodically updates DNS resolution results ---
class DNSUpdateMonitor:
    def __init__(self, nodes_config):
        self.nodes_config = nodes_config
        self.is_running = False
        self.monitor_thread = None

    def start(self):
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self._run_monitor)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        logger.info("DNS update monitor started.")

    def _run_monitor(self):
        # Initial update for all nodes immediately
        for node_name, node_info in self.nodes_config.items():
            cf_config = node_info.get("cloudflare", {})
            record_name = cf_config.get("record_name")
            enable_ipv4 = cf_config.get("enable_ipv4", True)
            enable_ipv6 = cf_config.get("enable_ipv6", True)
            if record_name:
                update_node_ips(node_name, record_name, enable_ipv4, enable_ipv6)

        # Subsequent updates at regular intervals
        while self.is_running:
            time.sleep(30) # e.g., update DNS resolution every 30 seconds
            logger.info("Refreshing DNS resolution results...")
            for node_name, node_info in self.nodes_config.items():
                cf_config = node_info.get("cloudflare", {})
                record_name = cf_config.get("record_name")
                enable_ipv4 = cf_config.get("enable_ipv4", True)
                enable_ipv6 = cf_config.get("enable_ipv6", True)
                if record_name:
                    update_node_ips(node_name, record_name, enable_ipv4, enable_ipv6)

    def stop(self):
        self.is_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("DNS update monitor stopped.")

    def get_node_ip(self, node_name, prefer_ipv6=False):
        """Gets the current best IP for the specified node."""
        with _dns_lock:
            node_data = _node_dns_ips.get(node_name, {})
            if prefer_ipv6 and node_data.get("ipv6"):
                return node_data["ipv6"]
            elif node_data.get("ipv4"):
                return node_data["ipv4"]
            return None

# --- NodeProxyServer Class: Creates a proxy for each node ---
class NodeProxyServer:
    def __init__(self, node_name, listen_ip, listen_port, dns_monitor):
        self.node_name = node_name
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.dns_monitor = dns_monitor
        self.server_socket = None
        self.is_running = False
        self.server_thread = None

    def start(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.listen_ip, self.listen_port))
            self.server_socket.listen(5)
            self.is_running = True
            logger.info(f"[{self.node_name}] Proxy server started, listening on {self.listen_ip}:{self.listen_port}")

            self.server_thread = threading.Thread(target=self._accept_connections)
            self.server_thread.daemon = True
            self.server_thread.start()
            return True
        except OSError as e: # Catch specific OSError for address in use
            if e.errno == 10048: # Windows specific error for "Address already in use"
                logger.critical(f"[{self.node_name}] Failed to start proxy server: Port {self.listen_port} is already in use. Please check if another program (e.g., cfnat) is using it, or change the listen_port in config.json.")
            else:
                logger.critical(f"[{self.node_name}] Failed to start proxy server (OSError): {e}")
            return False
        except Exception as e:
            logger.critical(f"[{self.node_name}] Failed to start proxy server (General Error): {e}")
            return False

    def _accept_connections(self):
        while self.is_running:
            try:
                rlist, _, _ = select.select([self.server_socket], [], [], 1)
                if self.server_socket in rlist:
                    client_socket, client_addr = self.server_socket.accept()
                    logger.info(f"[{self.node_name}] Received connection request from {client_addr}.")
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, client_addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
            except Exception as e:
                if self.is_running:
                    logger.error(f"[{self.node_name}] Error accepting connection: {e}")
                break

    def _handle_client(self, client_socket, client_addr):
        remote_socket = None
        try:
            request_buffer = b""
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    raise ConnectionResetError("Client closed connection prematurely")
                request_buffer += chunk
                if b"\r\n\r\n" in request_buffer:
                    break

            first_line = request_buffer.split(b"\r\n")[0]
            method, path, _ = first_line.split(b' ', 2)

            if method == b'CONNECT':
                target_ip = self.dns_monitor.get_node_ip(self.node_name)
                if not target_ip:
                    logger.warning(f"[{self.node_name} - {client_addr}] Error: Best IP not available from DNS for node '{self.node_name}'.")
                    client_socket.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                    client_socket.close()
                    return

                logger.info(f"[{self.node_name} - {client_addr}] Attempting to connect via DNS best IP ({target_ip}:{CLOUDFLARE_CDN_PORT})...")

                remote_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                remote_socket.settimeout(5)
                remote_socket.connect((target_ip, CLOUDFLARE_CDN_PORT))
                remote_socket.settimeout(None)

                client_socket.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._forward_data(client_socket, remote_socket)
            else:
                logger.warning(f"[{self.node_name} - {client_addr}] Received non-CONNECT HTTP method ({method.decode()}). This method is not supported.")
                client_socket.sendall(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
                client_socket.close()

        except socket.timeout:
            logger.error(f"[{self.node_name} - {client_addr}] Connection to remote server timed out.")
            client_socket.sendall(f"HTTP/1.1 {http.client.GATEWAY_TIMEOUT} Gateway Timeout\r\n\r\n".encode())
        except ConnectionRefusedError:
            logger.error(f"[{self.node_name} - {client_addr}] Connection refused.")
            client_socket.sendall(f"HTTP/1.1 {http.client.BAD_GATEWAY} Bad Gateway\r\n\r\n".encode())
        except Exception as e:
            logger.error(f"[{self.node_name} - {client_addr}] Error handling client request: {e}")
            if client_socket:
                try:
                    client_socket.sendall(f"HTTP/1.1 {http.client.INTERNAL_SERVER_ERROR} Internal Server Error\r\n\r\n".encode())
                except Exception as send_e:
                    pass
        finally:
            if client_socket:
                client_socket.close()
            if 'remote_socket' in locals() and remote_socket:
                remote_socket.close()

    def _forward_data(self, sock1, sock2):
        sockets = [sock1, sock2]
        while True:
            try:
                rlist, _, _ = select.select(sockets, [], [], 1)
                if not rlist:
                    continue

                for sock in rlist:
                    data = sock.recv(4096)
                    if not data:
                        return
                    if sock == sock1:
                        sock2.sendall(data)
                    else:
                        sock1.sendall(data)
            except Exception as e:
                break

    def stop(self):
        self.is_running = False
        if self.server_socket:
            self.server_socket.close()
            logger.info(f"[{self.node_name}] Proxy server stopped.")
        if self.server_thread:
            self.server_thread.join(timeout=5)

# --- Main program logic ---
if __name__ == "__main__":
    logger.info("Starting cfnatddns proxy application...")
    # 1. Read configuration file
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        nodes_config = config.get("nodes", {})
        if not nodes_config:
            logger.error(f"Error: 'nodes' configuration not found in {CONFIG_FILE}.")
            exit()
    except FileNotFoundError:
        logger.error(f"Error: Configuration file {CONFIG_FILE} not found. Please ensure the file exists.")
        exit()
    except json.JSONDecodeError:
        logger.error(f"Error: {CONFIG_FILE} has invalid JSON format.")
        exit()
    except Exception as e:
        logger.error(f"An unknown error occurred while reading the configuration file: {e}")
        exit()

    # 2. Start DNS update monitor
    dns_monitor = DNSUpdateMonitor(nodes_config)
    dns_monitor.start()

    # 3. Start a proxy server for each node
    proxy_servers = []
    for node_name, node_info in nodes_config.items():
        listen_port = node_info.get("listen_port")
        if not listen_port:
            logger.warning(f"Node '{node_name}' does not have 'listen_port' configured, skipping.")
            continue
        
        proxy_server = NodeProxyServer(node_name, LOCAL_PROXY_BIND_IP, listen_port, dns_monitor)
        if proxy_server.start(): # This will now return False on port conflict
            proxy_servers.append(proxy_server)
        # No else block here, as the error is logged inside proxy_server.start()

    if not proxy_servers:
        logger.critical("No proxy servers started successfully. Please check logs for errors (e.g., port conflicts). Exiting.")
        dns_monitor.stop()
        exit()

    logger.info("\n--- Simulating v2ray with cfnat DNS dynamic IP running ---")
    logger.info("Please ensure the cfnat client is running and updating your Cloudflare DNS records.")
    for ps in proxy_servers:
        logger.info(f"Node [{ps.node_name}]: HTTP proxy listening on {ps.listen_ip}:{ps.listen_port}")
        logger.info(f"Please set your browser/app proxy to HTTP proxy {ps.listen_ip}:{ps.listen_port}")
    logger.info(" (This proxy only supports HTTPS (CONNECT method) forwarding)")
    logger.info(f"Logs are being written to {LOG_FILE}")
    logger.info("Press Ctrl+C to exit.")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Ctrl+C detected. Stopping program...")
    finally:
        for ps in proxy_servers:
            ps.stop()
        dns_monitor.stop()
        logger.info("Program exited safely.")

