# Privacy-Preserving Health Assistant Telegram Bot

A secure and privacy-focused Telegram bot that provides health monitoring, system monitoring, and secure external API access through Tor.

## Features

### 1. Privacy-Preserving Health Assistant
- 🔒 Encrypted health data storage
- 🛡️ Configurable privacy levels
- 📊 Health tracking and monitoring
- 💊 Medication management
- 🏥 Chronic condition support
- 🔐 Local data storage

### 2. System Monitoring
- 💻 CPU and memory monitoring
- 🌡️ Temperature tracking
- 💾 Disk usage monitoring
- 📊 System performance metrics

### 3. Training Monitor
- 📈 Training progress tracking
- 📊 Performance metrics
- 🔄 Real-time updates

### 4. Tor Integration
- 🕵️ Anonymous browsing
- 🔄 IP rotation
- 🔒 Secure API access
- 🛡️ Privacy protection

## Prerequisites

- Python 3.8 or higher
- Telegram Bot Token
- Tor service
- Required Python packages

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd telegram_bot
```

2. Install required Python packages:
```bash
pip install python-telegram-bot requests[socks] stem pysocks cryptography
```

3. Install Tor:
```bash
# For Ubuntu/Debian
sudo apt-get update
sudo apt-get install tor

# For macOS
brew install tor

# For Windows
# Download and install from https://www.torproject.org/download/
```

4. Configure Tor:
```bash
# Edit Tor configuration file
sudo nano /etc/tor/torrc

# Add these lines:
ControlPort 9051
HashedControlPassword your_hashed_password
```

5. Generate Tor control password hash:
```bash
tor --hash-password "your_secure_password"
```

6. Set up environment variables:
```bash
# Create a .env file
echo "TELEGRAM_BOT_TOKEN=your_bot_token" > .env
echo "TOR_CONTROL_PASSWORD=your_secure_password" >> .env
```

## Configuration

1. Update the bot token in `jarvis.py`:
```python
TOKEN = "your_telegram_bot_token"
```

2. Configure Tor settings in `jarvis.py`:
```python
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = "your_secure_password"
```

## Usage

### Starting the Bot

```bash
python jarvis.py
```

### Available Commands

1. Health Assistant:
   - `/privacy_health` - Start privacy-preserving health check
   - `/health_check` - Start basic health check

2. System Monitoring:
   - `/server_status` - Check system status
   - `/training_status` - Check training progress

3. Tor Features:
   - `/check_tor` - Check Tor connection status
   - `/renew_tor` - Get new Tor identity
   - `/tor_request <url>` - Make request through Tor

### Privacy Levels

The health assistant offers three privacy levels:

1. High Privacy (7 days retention):
   - Maximum data minimization
   - Full anonymization
   - Strict encryption

2. Medium Privacy (30 days retention):
   - Standard data minimization
   - Basic anonymization
   - Standard encryption

3. Low Privacy (90 days retention):
   - Minimal data minimization
   - No anonymization
   - Basic encryption

### Health Features

The bot supports monitoring of:
- Physical symptoms
- Chronic conditions
- Medication management
- Lifestyle factors
- Mental health
- Sleep patterns
- Exercise tracking
- Nutrition monitoring

## Security Features

1. Data Protection:
   - End-to-end encryption
   - Local storage only
   - Configurable retention periods
   - Secure key management

2. Tor Integration:
   - Anonymous routing
   - IP rotation
   - Secure API access
   - Traffic encryption

3. Privacy Controls:
   - Data minimization
   - Anonymization options
   - User consent management
   - Secure data deletion

## Directory Structure

```
telegram_bot/
├── jarvis.py              # Main bot file
├── health.py             # Health monitoring module
├── system_monitor.py     # System monitoring module
├── training_monitor.py   # Training monitoring module
├── health_logs/         # Encrypted health data storage
└── README.md            # This file
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## Security Considerations

1. Never share your bot token
2. Use strong passwords for Tor control
3. Regularly update dependencies
4. Monitor system logs
5. Keep Tor service updated

## Troubleshooting

1. Tor Connection Issues:
   - Check Tor service status: `sudo service tor status`
   - Verify ports: `netstat -tuln | grep 9050`
   - Check Tor logs: `sudo tail -f /var/log/tor/log`

2. Bot Connection Issues:
   - Verify bot token
   - Check internet connection
   - Ensure Python packages are installed
   - Check system logs

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Telegram Bot API
- Tor Project
- Python-Telegram-Bot library
- Stem library for Tor control

## Support

For support, please:
1. Check the troubleshooting section
2. Review the documentation
3. Open an issue on GitHub
4. Contact the maintainers

## Disclaimer

This bot is not a substitute for professional medical advice. Always consult healthcare providers for medical decisions. 