# Splunk Enterprise Installation Guide

This guide explains how to install Splunk Enterprise on a local Windows system.

## Prerequisites

Before installing Splunk Enterprise, ensure your system meets the following requirements:

- **Operating System**: Windows 10/11 (64-bit) or Windows Server 2016/2019/2022
- **Hardware**:
  - Minimum: 2 CPU cores, 8 GB RAM, 100 GB disk space
  - Recommended: 4+ CPU cores, 16+ GB RAM, SSD storage
- **Permissions**: Administrator privileges required for installation
- **Dependencies**: None required (Splunk includes all necessary components)

## Download Splunk Enterprise

Download the Splunk Enterprise MSI installer using the following command in PowerShell or Command Prompt:

```powershell
wget -O splunk-10.2.1-c892b66d163d-windows-x64.msi "https://download.splunk.com/products/splunk/releases/10.2.1/windows/splunk-10.2.1-c892b66d163d-windows-x64.msi"
```

Alternative download methods:
- **Web Browser**: Visit [Splunk Downloads Page](https://www.splunk.com/en_us/download/splunk-enterprise.html) and register for a free account to download
- **Direct Link**: The provided wget command downloads version 10.2.1 build c892b66d163d for Windows x64

## Installation Steps

### Method 1: Using the MSI Installer (Recommended)

1. **Locate the downloaded MSI file** (typically in your Downloads folder)
2. **Right-click the MSI file** and select "Install" or run as administrator
3. **Follow the installation wizard**:
   - Accept the license agreement
   - Choose installation directory (default: `C:\Program Files\Splunk`)
   - Set up administrator credentials (username and password)
   - Configure Splunk to start automatically (recommended)
   - Choose whether to enable SSL/TLS encryption
   - Review and confirm installation settings
4. **Wait for installation to complete** (typically 5-10 minutes)
5. **Click Finish** when installation completes

### Method 2: Silent Installation (Command Line)

For automated or scripted installations:

```powershell
msiexec /i splunk-10.2.1-c892b66d163d-windows-x64.msi AGREETOLICENSE=yes SPLUNK_ADMIN_PASSWORD=YourSecurePassword123! /quiet
```

Parameters:
- `AGREETOLICENSE=yes`: Accepts the license agreement
- `SPLUNK_ADMIN_PASSWORD`: Sets the admin password (change to a strong password)
- `/quiet`: Runs installation silently without user interaction

## Starting Splunk

### As a Service (Default)
If you chose to install Splunk as a service during installation:
- Splunk automatically starts as a Windows service
- To manage the service:
  1. Open Services.msc
  2. Look for "splunkd" service
  3. Use standard service controls (Start, Stop, Restart)

### Manual Start
To start Splunk manually from Command Prompt (run as administrator):

```powershell
cd "C:\Program Files\Splunk\bin"
splunk start
```

To stop Splunk:
```powershell
splunk stop
```

To restart Splunk:
```powershell
splunk restart
```

## Accessing Splunk Web Interface

Once Splunk is running:

1. **Open your web browser**
2. **Navigate to**: `http://localhost:8000` (or `https://localhost:8000` if SSL enabled)
3. **Login** with:
   - Username: `admin`
   - Password: The password you set during installation

## First-Time Configuration

After logging in for the first time:

### 1. Set Up Splunk License
- Splunk Enterprise includes a 60-day trial license
- To use the free license (limited to 500MB/day):
  - Go to Settings → Licensing
  - Click "Change license group"
  - Select "Free License" and save

### 2. Configure Essential Settings
- **Indexes**: Review default indexes under Settings → Indexes
- **Forwarders**: Consider setting up Universal Forwarders for data collection
- **Apps**: Explore Splunkbase for additional apps (Settings → Apps)

### 3. Security Recommendations
- Change the admin password immediately after first login
- Consider enabling SSL/TLS for web interface
- Configure firewall rules if accessing remotely
- Review and adjust default user roles and permissions

## Verifying Installation

To verify Splunk is working correctly:

1. **Check service status**:
   ```powershell
   splunk status
   ```

2. **Review splunkd.log** for errors:
   ```
   C:\Program Files\Splunk\var\log\splunk\splunkd.log
   ```

3. **Test data input**:
   - Add sample data via Settings → Data Inputs
   - Try uploading a sample log file
   - Search for the data in the Search & Reporting app

## Troubleshooting Common Issues

### Installation Fails
- **Run as Administrator**: Ensure you're running the installer with admin rights
- **Antivirus Interference**: Temporarily disable antivirus during installation
- **Insufficient Disk Space**: Verify you have adequate free space
- **Conflicting Services**: Check for other services using port 8000

### Cannot Access Web Interface
- **Service Not Running**: Check if splunkd service is started
- **Port Conflict**: Verify nothing else is using port 8000 (`netstat -ano | findstr :8000`)
- **Firewall**: Ensure Windows Firewall allows inbound connections on port 8000
- **Binding Issues**: Check splunkd.log for binding errors

### Performance Issues
- **Insufficient Resources**: Monitor CPU/Memory usage
- **Disk I/O**: Ensure storage has adequate performance
- **Excessive Data Volume**: Consider implementing data retention policies

## Uninstalling Splunk Enterprise

### Using Windows Settings
1. Go to Settings → Apps → Apps & features
2. Find "Splunk Enterprise" in the list
3. Click Uninstall and follow the wizard

### Using Command Line
```powershell
wmic product where "name like 'Splunk Enterprise%%'" call uninstall
```

### Manual Cleanup (if needed)
After uninstallation, you may need to manually remove:
- `C:\Program Files\Splunk\`
- `C:\ProgramData\Splunk\`
- Splunk-related Windows services (if not removed automatically)

## Additional Resources

- **Official Documentation**: https://docs.splunk.com/Documentation/Splunk
- **Splunk Answers Community**: https://answers.splunk.com/
- **Splunk Tutorials**: https://www.splunk.com/en_us/training.html
- **Splunkbase Apps**: https://splunkbase.splunk.com/

## Notes About This Version

This guide covers Splunk Enterprise version 10.2.1 (build c892b66d163d):
- Released: Approximately Q1 2024
- Features: Enhanced security, improved performance, updated UI components
- Compatibility: Works with Windows 10/11 and Server 2016/2019/2022
- End of Life: Check Splunk's support lifecycle for specific EOL dates

## License Information

Splunk Enterprise offers:
- **Free License**: 500MB/day indexing limit, limited features
- **Enterprise Trial**: 60-day full-featured trial
- **Paid Licenses**: Based on daily indexing volume or perpetual licenses

For production use, consider purchasing appropriate licensing based on your data volume requirements.

---
*Installation guide created for Setuq project*
*Last updated: March 2026*