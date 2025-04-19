import socket
import datetime
import psutil
import GPUtil
import subprocess
import requests
import time
import re

class SystemMonitor:
    def __init__(self):
        self.hostname = socket.gethostname()
        self.ip = socket.gethostbyname(self.hostname)
        # Cache for internet status to avoid too many requests
        self.internet_cache = {
            'status': None,
            'timestamp': None
        }
        # Cache expiration time in seconds (5 minutes)
        self.cache_expiration = 300

    def get_cpu_temperature(self):
        try:
            # Try to get CPU temperature from psutil
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                # Get average temperature of all CPU cores
                core_temps = temps['coretemp']
                avg_temp = sum(temp.current for temp in core_temps) / len(core_temps)
                return f"{avg_temp:.1f}°C"
            elif 'k10temp' in temps:  # For AMD processors
                return f"{temps['k10temp'][0].current:.1f}°C"
            else:
                return "N/A"
        except Exception as e:
            print(f"Error getting CPU temperature: {str(e)}")
            return "N/A"

    def get_gpu_info(self):
        gpu_info = ""
        try:
            print("Attempting to get GPU information...")
            gpus = GPUtil.getGPUs()
            print(f"Found {len(gpus)} GPUs")
            if gpus:
                gpu_info = "\n🎮 GPU Information:\n"
                for gpu in gpus:
                    print(f"Processing GPU {gpu.id}: {gpu.name}")
                    gpu_info += (
                        f"GPU {gpu.id}: {gpu.name}\n"
                        f"Load: {gpu.load*100:.1f}%\n"
                        f"Memory: {gpu.memoryUsed}MB / {gpu.memoryTotal}MB\n"
                        f"Temperature: {gpu.temperature}°C\n"
                    )
            else:
                print("No GPUs found")
                gpu_info = "\n🎮 GPU: No GPUs detected"
        except Exception as e:
            print(f"Error getting GPU information: {str(e)}")
            gpu_info = "\n🎮 GPU: Not available or not supported"
        return gpu_info

    def get_wifi_info(self):
        wifi_info = ""
        try:
            # First try using nmcli (NetworkManager) which is more reliable on many systems
            nmcli_result = subprocess.run(['nmcli', '-t', '-f', 'NAME,DEVICE,TYPE,STATE', 'connection', 'show', '--active'], 
                                         capture_output=True, text=True)
            
            if nmcli_result.returncode == 0 and nmcli_result.stdout.strip():
                # Parse the output to get WiFi information
                wifi_connections = []
                for line in nmcli_result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 4 and parts[2] == '802-11-wireless':
                        wifi_connections.append({
                            'name': parts[0],
                            'device': parts[1],
                            'state': parts[3]
                        })
                
                if wifi_connections:
                    wifi_info = "\n📶 WiFi Status: Connected"
                    for conn in wifi_connections:
                        wifi_info += f"\nSSID: {conn['name']}"
                        wifi_info += f"\nDevice: {conn['device']}"
                        
                        # Try to get signal strength using iwconfig
                        try:
                            iwconfig_result = subprocess.run(['iwconfig', conn['device']], 
                                                            capture_output=True, text=True)
                            if iwconfig_result.returncode == 0:
                                signal_match = re.search(r'Signal level=([-\d]+)', iwconfig_result.stdout)
                                if signal_match:
                                    wifi_info += f"\nSignal: {signal_match.group(1)} dBm"
                                
                                bit_rate_match = re.search(r'Bit Rate=([\d.]+)', iwconfig_result.stdout)
                                if bit_rate_match:
                                    wifi_info += f"\nBit Rate: {bit_rate_match.group(1)} Mb/s"
                        except Exception:
                            pass  # Ignore errors in getting detailed WiFi info
                else:
                    wifi_info = "\n📶 WiFi Status: Not connected"
            else:
                # Fall back to iwconfig if nmcli fails
                iwconfig_result = subprocess.run(['iwconfig'], capture_output=True, text=True)
                if iwconfig_result.returncode == 0:
                    # Parse the output to get WiFi information
                    wifi_output = iwconfig_result.stdout
                    
                    # Check if WiFi is connected
                    if "ESSID" in wifi_output:
                        # Extract SSID
                        ssid_match = re.search(r'ESSID:"([^"]+)"', wifi_output)
                        ssid = ssid_match.group(1) if ssid_match else "Unknown"
                        
                        # Extract signal strength
                        signal_match = re.search(r'Signal level=([-\d]+)', wifi_output)
                        signal = signal_match.group(1) if signal_match else "Unknown"
                        
                        # Extract bit rate
                        bit_rate_match = re.search(r'Bit Rate=([\d.]+)', wifi_output)
                        bit_rate = bit_rate_match.group(1) if bit_rate_match else "Unknown"
                        
                        wifi_info = (
                            f"\n📶 WiFi Status: Connected\n"
                            f"SSID: {ssid}\n"
                            f"Signal: {signal} dBm\n"
                            f"Bit Rate: {bit_rate} Mb/s"
                        )
                    else:
                        wifi_info = "\n📶 WiFi Status: Not connected"
                else:
                    # Try alternative method for systems without iwconfig
                    network_interfaces = psutil.net_if_stats()
                    wifi_interfaces = [iface for iface in network_interfaces.keys() 
                                      if iface.startswith('wlan') or iface.startswith('wifi')]
                    
                    if wifi_interfaces:
                        wifi_info = "\n📶 WiFi Status: Connected"
                        for iface in wifi_interfaces:
                            stats = network_interfaces[iface]
                            wifi_info += f"\nInterface: {iface}"
                            wifi_info += f"\nSpeed: {stats.speed} Mb/s"
                            wifi_info += f"\nMTU: {stats.mtu}"
                    else:
                        # Check if we have any active network connections
                        connections = psutil.net_connections(kind='inet')
                        if connections:
                            wifi_info = "\n📶 Network: Connected (WiFi status unknown)"
                        else:
                            wifi_info = "\n📶 WiFi Status: Not available"
        except Exception as e:
            print(f"Error getting WiFi information: {str(e)}")
            # Check if we have any active network connections as a fallback
            try:
                connections = psutil.net_connections(kind='inet')
                if connections:
                    wifi_info = "\n📶 Network: Connected (WiFi status unknown)"
                else:
                    wifi_info = "\n📶 WiFi Status: Error checking"
            except:
                wifi_info = "\n📶 WiFi Status: Error checking"
        
        return wifi_info

    def check_internet_connection(self):
        # Check if we have a cached result that hasn't expired
        if self.internet_cache['status'] and self.internet_cache['timestamp']:
            elapsed_time = time.time() - self.internet_cache['timestamp']
            if elapsed_time < self.cache_expiration:
                print("Using cached internet status")
                return self.internet_cache['status']
        
        try:
            # Try to connect to a reliable website with a short timeout
            response = requests.get("https://www.google.com", timeout=5)
            if response.status_code == 200:
                # Calculate ping to Google
                ping_result = subprocess.run(['ping', '-c', '1', '8.8.8.8'], 
                                           capture_output=True, text=True)
                ping_match = re.search(r'time=([\d.]+)', ping_result.stdout)
                ping_time = ping_match.group(1) if ping_match else "Unknown"
                
                internet_status = f"\n🌐 Internet: Connected (Ping: {ping_time} ms)"
            else:
                internet_status = "\n🌐 Internet: Connected but slow"
        except requests.RequestException:
            internet_status = "\n🌐 Internet: Disconnected"
        except Exception as e:
            print(f"Error checking internet connection: {str(e)}")
            internet_status = "\n🌐 Internet: Error checking"
        
        # Cache the result
        self.internet_cache['status'] = internet_status
        self.internet_cache['timestamp'] = time.time()
        
        return internet_status

    def get_network_interfaces(self):
        network_info = "\n🌐 Network Interfaces:"
        try:
            # Get all network interfaces
            interfaces = psutil.net_if_addrs()
            for interface_name, interface_addresses in interfaces.items():
                # Skip loopback interface
                if interface_name == 'lo':
                    continue
                
                network_info += f"\n  {interface_name}:"
                for addr in interface_addresses:
                    if addr.family == socket.AF_INET:  # IPv4
                        network_info += f"\n    IPv4: {addr.address}"
                    elif addr.family == socket.AF_INET6:  # IPv6
                        network_info += f"\n    IPv6: {addr.address}"
        except Exception as e:
            print(f"Error getting network interfaces: {str(e)}")
            network_info += "\n  Error getting network information"
        
        return network_info

    def get_status(self):
        uptime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cpu = psutil.cpu_percent()
        cpu_temp = self.get_cpu_temperature()
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        gpu_info = self.get_gpu_info()
        wifi_info = self.get_wifi_info()
        internet_status = self.check_internet_connection()
        network_interfaces = self.get_network_interfaces()

        status_message = (
            f"🖥 Host: {self.hostname}\n"
            f"📅 Time: {uptime}\n"
            f"🔥 CPU: {cpu}% (Temp: {cpu_temp})\n"
            f"🧠 Mem: {mem.percent}% ({mem.used // 1024**2}MB / {mem.total // 1024**2}MB)\n"
            f"💾 Disk: {disk.percent}% ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)"
            f"{gpu_info}"
            f"{network_interfaces}"
            f"{wifi_info}"
            f"{internet_status}"
        )
        print("Final status message:", status_message)
        return status_message 