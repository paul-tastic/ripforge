# RipForge Drive Troubleshooting Guide

This document covers common drive issues, their symptoms, and resolution steps.

## Quick Reference: Reset Methods (Least to Most Aggressive)

| Level | Method | Command | Clears |
|-------|--------|---------|--------|
| 1 | Eject/Load | `eject /dev/sr0 && eject -t /dev/sr0` | Disc session |
| 2 | Device Reset | `sg_reset -d -N /dev/sr0` | LU state |
| 3 | Target Reset | `sg_reset -t -N /dev/sr0` | SATA port state |
| 4 | SCSI Unbind/Rebind | See below | Kernel driver state |
| 5 | Host Reset | `echo 1 > /sys/.../host_reset` | ATA controller |
| 6 | Full Reboot | `sudo reboot` | Everything |

---

## Error Types and Fixes

### 1. AACS Authentication Error

**Symptoms:**
```
Read of scrambled sector without authentication
```

**Cause:** The drive's AACS authentication session was corrupted or lost, typically after a hardware error or timeout.

**Fix Steps:**
1. Eject and reload the disc
2. If persists, try device reset: `sudo sg_reset -d -N /dev/sr0`
3. If persists, try SCSI unbind/rebind (use RipForge API or manual)
4. If persists, full reboot required

---

### 2. SCSI Timeout / Hardware Error

**Symptoms:**
```
sr 5:0:0:0: [sr0] tag#0 FAILED Result: hostbyte=DID_OK driverbyte=DRIVER_OK cmd_age=30s
sr 5:0:0:0: [sr0] tag#0 CDB: Read(10) 28 00 00 ab cd ef 00 00 20 00
blk_print_req_error: I/O error, dev sr0, sector NNNNN op 0x0 (READ) flags 0x80700
HARDWARE ERROR: TIMEOUT ON LOGICAL UNIT
```

**Cause:** The disc has a defect (scratch, manufacturing issue) or the drive failed to read a sector within the timeout period.

**Fix Steps:**
1. Eject disc and inspect for scratches/damage
2. Clean disc if dirty
3. Check dmesg for the failing sector: `sudo dmesg | grep -i sr0 | tail -50`
4. If sector is consistent, disc is likely defective at that location
5. Reset drive state: `sudo sg_reset -d -N /dev/sr0`
6. If drive becomes unresponsive, use SCSI unbind/rebind
7. If drive still unresponsive, reboot may be required

**Note:** A timeout error often corrupts the AACS state, causing subsequent discs to fail with authentication errors.

---

### 3. Drive Not Responding / Hung

**Symptoms:**
- `eject` command hangs
- MakeMKV hangs on disc scan
- `lsblk` shows drive but operations timeout

**Fix Steps:**
1. Kill any MakeMKV processes: `pkill -9 makemkvcon`
2. Try device reset: `sudo sg_reset -d -N /dev/sr0`
3. Try SCSI unbind/rebind
4. Check dmesg for errors
5. If still hung, full reboot required

---

### 4. Wrong Track Ripped

**Symptoms:**
- Output file much shorter/longer than expected
- Wrong content (e.g., trailer instead of movie)

**Cause:** Track indices differ between disc scan and backup scan, or scoring selected wrong track.

**Fix (implemented in v1.0.9):**
- RipForge now re-scans backup folders to get correct track indices
- Minimum 10-minute floor for movie tracks
- Percentage-based runtime matching

---

### 5. Backup Reports No Progress

**Symptoms:**
```
BACKUP: MakeMKV reported success but no progress
```

**Cause:** MakeMKV's backup command doesn't emit PRGV messages by default.

**Fix (implemented in v1.0.11):**
- RipForge now verifies backup by checking folder structure and size
- Supports both BDMV (Blu-ray) and VIDEO_TS (DVD) structures

---

## Manual Reset Procedures

### SCSI Unbind/Rebind (Level 4)

```bash
# Find SCSI ID (e.g., 5:0:0:0)
ls -la /sys/block/sr0/device | grep -o '[0-9]:[0-9]:[0-9]:[0-9]'

# Unbind
echo '5:0:0:0' | sudo tee /sys/bus/scsi/drivers/sr/unbind

# Wait
sleep 2

# Rebind  
echo '5:0:0:0' | sudo tee /sys/bus/scsi/drivers/sr/bind

# Wait for device to come back
sleep 2

# Verify
ls -la /dev/sr0
```

### Host Reset (Level 5)

```bash
# Find the host reset file for your drive (ata6 = host5 for BD drive)
find /sys -name 'host_reset' -path '*host5*' 2>/dev/null

# Trigger reset
echo 1 | sudo tee /sys/devices/pci0000:00/0000:00:02.1/0000:02:00.1/ata6/host5/scsi_host/host5/host_reset
```

---

## Diagnostic Commands

### Check Drive Status
```bash
# Is drive present?
ls -la /dev/sr0

# Drive info
sudo hdparm -I /dev/sr0

# SCSI device info
sg_inq /dev/sr0

# Check for errors in dmesg
sudo dmesg | grep -i 'sr0\|scsi\|error\|timeout' | tail -50
```

### Check for Hung Processes
```bash
# MakeMKV processes
ps aux | grep makemkv

# Processes using the drive
sudo lsof /dev/sr0
```

### Verify Disc Can Be Read
```bash
# Quick test - read disc info
sg_read_buffer -m echo /dev/sr0

# Test MakeMKV can scan
makemkvcon -r info disc:0 2>&1 | head -20
```

---

## RipForge API Endpoints

### Reset Drive (implemented in v1.0.10)
```bash
# Deep reset with SCSI unbind/rebind
curl -X POST 'http://localhost:5000/api/v1/drive/reset?deep=true'
```

---

## Known Problem Discs

| Disc | Issue | Workaround |
|------|-------|------------|
| Rogue One | SCSI timeout at ~22GB sector | Disc may be defective, try different copy |

---

## Prevention Tips

1. **Always use backup mode** (`rip_mode: always_backup`) - creates full backup before extracting, allows retry without re-reading disc
2. **Check disc condition** before ripping - clean dirty discs
3. **Monitor dmesg** during long rips for early error detection
4. **Keep drive firmware updated** if manufacturer provides updates

