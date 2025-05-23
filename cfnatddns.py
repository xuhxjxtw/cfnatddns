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
LOCAL_PROXY_BIND_IP = "127.0.0.1"
# This is the port our Python proxy will listen on for browser/app connections.
# This MUST NOT conflict with cfnat's listen_ports (e.g., 1234, 1235).
LOCAL_PROXY_PORT = 8080 

# Cloudflare CDN common HTTPS port (used by cfnat for its outbound)
# This is here for context, but our Python script will connect to cfnat's local port.
CLOUDFLARE_CDN_PORT = 443 
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
_node_dns_ips = {} # Stores DNS resolution results for each node (for logging/monitoring)
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
        logger.debug(f"DNS query failed: {hostname} does not exist for type {record_type}.") # Changed to debug
    except dns.resolver.Timeout:
        logger.warning(f"DNS query timed out: {hostname} for type {record_type}.")
    except Exception as e:
        logger.error(f"DNS query for {hostname} ({record_type}) encountered an error: {e}")
    return []

def update_node_ips(node_name, record_name, enable_ipv4, enable_ipv6):
    """
    Queries and updates the best IP for the specified node.
    This is primarily for logging/monitoring what cfnat is doing.
    """
    global _node_dns_ips
    current_ipv4 = None
    current_ipv6 = None

    if enable_ipv4:
        ipv4_ips = resolve_dns(record_name, 'A')
        if ipv4_ips:
            current_ipv4 = ipv4_ips[0]
            logger.info(f"[{node_name}] Resolved IPv4 for '{record_name}': {current_ipv4}")
        else:
            logger.warning(f"[{node_name}] Failed to resolve IPv4 address for '{record_name}'.")

    if enable_ipv6:
        ipv6_ips = resolve_dns(record_name, 'AAAA')
        if ipv6_ips:
            current_ipv6 = ipv6_ips[0]
            logger.info(f"[{node_name}] Resolved IPv6 for '{record_name}': {current_ipv6}")
        else:
            logger.warning(f"[{node_name}] Failed to resolve IPv6 address for '{record_name}'.")

    with _dns_lock:
        _node_dns_ips[node_name] = {"ipv4": current_ipv4, "ipv6": current_ipv6}
        if current_ipv4 or current_ipv6:
            logger.info(f"[{node_name}] Current DNS resolved IP: IPv4={current_ipv4 if current_ipv4 else 'None'}, IPv6={current_ipv6 if current_ipv6 else 'None'}")


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
            logger.info("Refreshing DNS resolution results for cfnat-updated domains...")
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

# --- MainProxyServer Class: Our Python proxy that forwards to cfnat ---
class MainProxyServer:
    def __init__(self, listen_ip, listen_port, cfnat_target_ip, cfnat_target_port):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.cfnat_target_ip = cfnat_target_ip
        self.cfnat_target_port = cfnat_target_port
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
            logger.info(f"Main Proxy Server started, listening on {self.listen_ip}:{self.listen_port}")
            logger.info(f"All traffic will be forwarded to cfnat at {self.cfnat_target_ip}:{self.cfnat_target_port}")

            self.server_thread = threading.Thread(target=self._accept_connections)
            self.server_thread.daemon = True
            self.server_thread.start()
            return True
        except OSError as e:
            if e.errno == 10048: # Windows specific error for "Address already in use"
                logger.critical(f"Failed to start Main Proxy Server: Port {self.listen_port} is already in use. Please choose a different LOCAL_PROXY_PORT.")
            else:
                logger.critical(f"Failed to start Main Proxy Server (OSError): {e}")
            return False
        except Exception as e:
            logger.critical(f"Failed to start Main Proxy Server (General Error): {e}")
            return False

    def _accept_connections(self):
        while self.is_running:
            try:
                rlist, _, _ = select.select([self.server_socket], [], [], 1)
                if self.server_socket in rlist:
                    client_socket, client_addr = self.server_socket.accept()
                    logger.info(f"Received connection from {client_addr}.")
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, client_addr)
                    )
                    client_thread.daemon = True
                    client_thread.start()
            except Exception as e:
                if self.is_running:
                    logger.error(f"Error accepting connection: {e}")
                break

    def _handle_client(self, client_socket, client_addr):
        remote_socket = None
        try:
            request_buffer = b""
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    # Client closed connection prematurely, or connection reset by peer
                    logger.warning(f"[{client_addr}] Client closed connection prematurely or connection reset during header receive.")
                    return
                request_buffer += chunk
                if b"\r\n\r\n" in request_buffer:
                    break

            first_line = request_buffer.split(b"\r\n")[0]
            method, path, _ = first_line.split(b' ', 2)

            if method == b'CONNECT':
                target_host_port = path.decode('utf-8')
                logger.info(f"[{client_addr}] Received CONNECT request for {target_host_port}. Forwarding to cfnat at {self.cfnat_target_ip}:{self.cfnat_target_port}...")

                # Establish connection to cfnat's local listening port
                remote_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                remote_socket.settimeout(5) # Set connection timeout to cfnat
                remote_socket.connect((self.cfnat_target_ip, self.cfnat_target_port))
                remote_socket.settimeout(None) # Remove timeout after connection
                logger.info(f"[{client_addr}] Successfully connected to cfnat at {self.cfnat_target_ip}:{self.cfnat_target_port}.")

                # Reply to client that connection is established
                client_socket.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

                # Start forwarding data between client and cfnat
                self._forward_data(client_socket, remote_socket)
            else:
                logger.warning(f"[{client_addr}] Received non-CONNECT HTTP method ({method.decode()}). This proxy only supports HTTPS (CONNECT method).")
                client_socket.sendall(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
                client_socket.close()

        except socket.timeout:
            logger.error(f"[{client_addr}] Connection to cfnat ({self.cfnat_target_ip}:{self.cfnat_target_port}) timed out.")
            if client_socket:
                client_socket.sendall(f"HTTP/1.1 {http.client.GATEWAY_TIMEOUT} Gateway Timeout\r\n\r\n".encode())
        except ConnectionRefusedError:
            logger.error(f"[{client_addr}] Connection to cfnat ({self.cfnat_target_ip}:{self.cfnat_target_port}) refused. Is cfnat running and listening on this port?")
            if client_socket:
                client_socket.sendall(f"HTTP/1.1 {http.client.BAD_GATEWAY} Bad Gateway\r\n\r\n".encode())
        except Exception as e:
            logger.error(f"[{client_addr}] Error handling client request: {e}", exc_info=True) # Log full traceback
            if client_socket:
                try:
                    client_socket.sendall(f"HTTP/1.1 {http.client.INTERNAL_SERVER_ERROR} Internal Server Error\r\n\r\n".encode())
                except Exception as send_e:
                    logger.error(f"[{client_addr}] Failed to send error response: {send_e}")
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
                        logger.info(f"Connection closed by {sock.getpeername()}.")
                        return # Connection closed
                    if sock == sock1: # Data from client to cfnat
                        sock2.sendall(data)
                    else: # Data from cfnat to client
                        sock1.sendall(data)
            except ConnectionResetError:
                logger.warning(f"Connection reset during data forwarding between {sock1.getpeername()} and {sock2.getpeername()}.")
                return
            except Exception as e:
                logger.error(f"Error during data forwarding: {e}", exc_info=True) # Log full traceback
                return

    def stop(self):
        self.is_running = False
        if self.server_socket:
            self.server_socket.close()
            logger.info(f"Main Proxy Server stopped.")
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

    # Get the name and listen_port of the first node from config.json
    # This will be the cfnat's local listening port that our proxy connects to.
    primary_node_name = next(iter(nodes_config), None)
    if not primary_node_name:
        logger.critical("No nodes configured in config.json. Exiting.")
        exit()
    
    primary_cfnat_listen_port = nodes_config[primary_node_name].get("listen_port")
    if not primary_cfnat_listen_port:
        logger.critical(f"Node '{primary_node_name}' does not have 'listen_port' configured. This port is needed for connecting to cfnat. Exiting.")
        exit()

    # 2. Start DNS update monitor (still monitors all nodes, for logging/monitoring cfnat's DNS updates)
    dns_monitor = DNSUpdateMonitor(nodes_config)
    dns_monitor.start()

    # 3. Start our main proxy server, forwarding to cfnat's local listener
    main_proxy_server = MainProxyServer(LOCAL_PROXY_BIND_IP, LOCAL_PROXY_PORT, 
                                        LOCAL_PROXY_BIND_IP, primary_cfnat_listen_port) # cfnat is usually on localhost
    
    if not main_proxy_server.start():
        logger.critical("Main proxy server failed to start, exiting.")
        dns_monitor.stop()
        exit()

    logger.info("\n--- cfnatddns proxy is running ---")
    logger.info("This proxy acts as an HTTP proxy, forwarding traffic to your local cfnat instance.")
    logger.info("Please ensure the cfnat client is running and listening on its configured port.")
    logger.info(f"Our proxy listens on: {LOCAL_PROXY_BIND_IP}:{LOCAL_PROXY_PORT}")
    logger.info(f"It forwards to cfnat's local listener at: {LOCAL_PROXY_BIND_IP}:{primary_cfnat_listen_port}")
    logger.info(f"Please set your browser/app proxy to HTTP proxy {LOCAL_PROXY_BIND_IP}:{LOCAL_PROXY_PORT}")
    logger.info(" (This proxy only supports HTTPS (CONNECT method) forwarding)")
    logger.info(f"Logs are being written to {LOG_FILE}")
    logger.info("Press Ctrl+C to exit.")

    try:
        while True:
            # Periodically log the current DNS-resolved IP for the primary cfnat node
            # This helps to see what IP cfnat *should* be using for outbound connections.
            current_ipv4, current_ipv6 = dns_monitor.get_node_ip(primary_node_name)
            if current_ipv4 or current_ipv6:
                logger.info(f"Primary cfnat node '{primary_node_name}' DNS resolved to: IPv4={current_ipv4 if current_ipv4 else 'None'}, IPv6={current_ipv6 if current_ipv6 else 'None'}. (cfnat should be using this for outbound)")
            else:
                logger.warning(f"Primary cfnat node '{primary_node_name}' has no resolved IP yet. Check cfnat status and DNS.")

            time.sleep(10) # Log this status every 10 seconds

    except KeyboardInterrupt:
        logger.info("Ctrl+C detected. Stopping program...")
    finally:
        main_proxy_server.stop()
        dns_monitor.stop()
        logger.info("Program exited safely.")

