#!/bin/sh
# Integration test on the deployed Pi; sudo required. Mount changes are isolated.
set -eu
cd "$(dirname "$0")/.."
guard="$PWD/deploy/check-smarthome-storage"
sudo sh "$guard"
sudo unshare --mount --propagation private sh -eu -c '
    umount -l /mnt/external
    if sh "$1"; then echo "FAIL: accepted missing disk"; exit 1; fi
' sh "$guard"
sudo unshare --mount --propagation private sh -eu -c '
    mount -t tmpfs tmpfs /mnt/external
    mkdir -p /mnt/external/smarthome-data/prometheus
    if sh "$1"; then echo "FAIL: accepted wrong disk"; exit 1; fi
' sh "$guard"
sudo unshare --mount --propagation private sh -eu -c '
    mount --bind /mnt/external /mnt/external
    mount -o remount,bind,ro /mnt/external
    if sh "$1"; then echo "FAIL: accepted read-only disk"; exit 1; fi
' sh "$guard"
sudo sh "$guard"
echo 'PASS: correct disk accepted; missing, wrong, and read-only disks rejected'
