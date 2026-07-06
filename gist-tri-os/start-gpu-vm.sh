#!/bin/bash
# Bind the gfx1201 to VFIO WITHOUT a function-level reset, then start the
# passthrough VM. This is the host-side (Linux) helper used on the x86
# validation box to hand the card to the Linux or Windows guest.
#
# THE KEY TRICK (non-obvious): VFIO normally does an FLR (Function Level Reset)
# when it assigns a device to a VM. On gfx1201 that FLR wipes the VBIOS POST
# state the bring-up depends on (PSP SOS, SMU features, GC power). By clearing
# the device's reset_method BEFORE starting the VM, the VBIOS state from the
# last cold POST is preserved into the guest. So the flow is: cold power-cycle
# the host (fresh POST) -> run this -> the guest sees a freshly-POSTed card.
#
# Redacted for publication: set these for your machine (the password lives in a
# gitignored env file, never in the script):
#   GPU        - your gfx1201 PCI BDF (find via: lspci -nn | grep -i 1002:7551)
#   GPU_AUDIO  - the HDMI-audio function on the same card (usually .1)
#   VM         - your libvirt domain name
#   VM_IP      - guest IP (libvirt default NAT is 192.168.122.x)
#   VM_SSH_PASS- guest SSH password (export from a gitignored env file)

set -e

GPU="${GPU:-0000:c3:00.0}"
GPU_AUDIO="${GPU_AUDIO:-0000:c3:00.1}"
VM="${VM:-win11-gpu}"
VM_IP="${VM_IP:-192.168.122.16}"
: "${VM_SSH_PASS:?set VM_SSH_PASS in your gitignored env (e.g. source ./vm.env)}"

echo "=== Step 1: VFIO bind ($GPU) ==="
sudo modprobe vfio-pci
echo "vfio-pci" | sudo tee /sys/bus/pci/devices/$GPU/driver_override > /dev/null
echo "vfio-pci" | sudo tee /sys/bus/pci/devices/$GPU_AUDIO/driver_override > /dev/null
echo "$GPU"       | sudo tee /sys/bus/pci/drivers/vfio-pci/bind 2>/dev/null || true
echo "$GPU_AUDIO" | sudo tee /sys/bus/pci/drivers/snd_hda_intel/unbind 2>/dev/null || true
echo "$GPU_AUDIO" | sudo tee /sys/bus/pci/drivers/vfio-pci/bind 2>/dev/null || true
echo "VFIO bind: $(readlink /sys/bus/pci/devices/$GPU/driver)"

echo ""
echo "=== Step 2: Disable PCIe reset for the GPU (preserve VBIOS POST state) ==="
echo "" | sudo tee /sys/bus/pci/devices/$GPU/reset_method > /dev/null
if [ -f /sys/bus/pci/devices/$GPU_AUDIO/reset_method ]; then
    echo "" | sudo tee /sys/bus/pci/devices/$GPU_AUDIO/reset_method > /dev/null
fi
echo "Reset methods disabled"

echo ""
echo "=== Step 3: Start VM ($VM) ==="
sudo virsh start "$VM"

echo ""
echo "=== Waiting for guest SSH ($VM_IP) ==="
ready=0
for i in $(seq 1 60); do
    if sshpass -p "$VM_SSH_PASS" ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no \
         "nod@$VM_IP" 'echo VM_READY' 2>/dev/null; then
        ready=1
        break
    fi
    sleep 5
done
[ "$ready" = 1 ] || { echo "ERROR: guest did not come up at $VM_IP"; exit 1; }
echo "=== VM is ready ==="
