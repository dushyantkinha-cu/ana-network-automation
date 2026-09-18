#!/bin/bash
set -euo pipefail

IMG1="/var/lib/raid1-sim/disk1.img"
IMG2="/var/lib/raid1-sim/disk2.img"
MDDEV="/dev/md0"
MOUNTPOINT="/mnt/influxdb-raid1"

get_loop() {
    sudo losetup -j "$1" | cut -d: -f1 | head -n1
}

LOOP1="$(get_loop "$IMG1")"
if [ -z "$LOOP1" ]; then
    LOOP1="$(sudo losetup --find --show "$IMG1")"
fi

LOOP2="$(get_loop "$IMG2")"
if [ -z "$LOOP2" ]; then
    LOOP2="$(sudo losetup --find --show "$IMG2")"
fi

echo "RAID member 1: $LOOP1"
echo "RAID member 2: $LOOP2"

if ! sudo mdadm --detail "$MDDEV" >/dev/null 2>&1; then
    sudo mdadm --assemble "$MDDEV" "$LOOP1" "$LOOP2"
fi

sudo mkdir -p "$MOUNTPOINT"

if ! mountpoint -q "$MOUNTPOINT"; then
    sudo mount "$MDDEV" "$MOUNTPOINT"
fi
