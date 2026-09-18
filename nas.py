#!/usr/bin/env python3
import sys
import time
import argparse
import ast
import os
import serial

# --- CONFIGURATION ---
SERIAL_PORT = '/dev/ttyUSB0'  # Adjust to /dev/ttyACM0 if necessary
BAUDRATE = 115200
HBA_PCI_ADDR = "0000:01:00.0"  # Run 'lspci' to confirm your LSI 2308 address
BRIDGE_PCI_ADDR = "0000:00:13.0"  # Your upstream Intel Root Port
#HBA_DRIVER = "mvsas"
HBA_DRIVER = "mpt3sas"


class StorageCLI:
    def __init__(self, port=SERIAL_PORT, baud=BAUDRATE):
        try:
            self.ser = serial.Serial(port, baud, timeout=5)
            time.sleep(1.5) # Allow ESP32 bootloader to skip serial hook reset

        except Exception as e:
            print(f"❌ Error connecting to ESP32 on {port}: {e}")
            sys.exit(1)

    def send(self, cmd_str):
        """Sends a structured instruction to the ESP32 and extracts the JSON data dictionary."""
        self.ser.write(f"{cmd_str}\n".encode('utf-8'))
        while True:
            line = self.ser.readline().decode('utf-8').strip()
            if line.startswith("__RESP__"):
                _, status, data_str = line.split(":", 2)
                return status, ast.literal_eval(data_str)

    def status_pins(self):
        """Fetches pin statuses"""
        status, data = self.send("POLL_STATES")
#        print(status)
#        print(data)
        if status != "OK":
            print("❌ Failed to get states")
            return None
        return data

    def status_va(self, pins):
        """Fetches and mathematically translates the INA3221 telemetry registers into crisp data."""
        status, data = self.send("POLL_VA")
#        print(status)
#        print(data)
        if status != "OK":
            print("❌ Failed to pull telemetry from sensors.")
            return

        print("\n" + "="*55)
        print(" 📊 REAL-TIME SUBSYSTEM STORAGE TELEMETRY")
        print("="*55)


        if pins["atx_psu"] == 0:
            p_psu = 0
            print("off ATX PSU 0W")
        else:
            p_psu = 5.0
            print("on  ATX PSU 5W baseline")

        # HBA Calculations
        state = "on " if pins["pcie_pw"] == 1 else "off"
        p_19 = data['v_19_5v_brick'] * data['c_19_5v_brick']
        print(f"{state} Buck input 19.5v:    {data['v_19_5v_brick']:5.2f} V | {data['c_19_5v_brick']:5.3f} A | {p_19:.2f} W")

        p_33 = data['v_hba_3_3v'] * data['c_hba_3_3v']
        print(f"{state} PCIE 3.3V rail:      {data['v_hba_3_3v']:5.2f} V | {data['c_hba_3_3v']:5.3f} A | {p_33:.2f} W")
        print("-"*55)

        # Drive Array Calculations
        state = "on " if pins["hdd_1"] == 1 else "off"
        c_b1 = data['c_hdd_b1_12v']
        v_b1_12 = data['v_hdd_b1_12v']
        v_b1_5  = data['v_hdd_b1_5v']
        c_b1_5  = data['c_hdd_b1_5v']
        p_b1 = (v_b1_12 * c_b1) + (v_b1_5 * c_b1_5)
        print(f"{state} Bundle 1 (12V):      {v_b1_12:5.2f} V | {c_b1:5.3f} A")
        print(f"    Bundle 1 (5V Mon):   {v_b1_5:5.2f} V | {c_b1_5:5.3f} A | Total Power: {p_b1:.2f} W")

        state = "on " if pins["hdd_2"] == 1 else "off"
        c_b2 = data['c_hdd_b2_12v']
        p_b2 = data['v_hdd_b2_12v'] * c_b2
        if p_b2 > 1:
            p_b2 *= 1.4 ## 5v rail
        print(f"{state} Bundle 2 (12V Only): {data['v_hdd_b2_12v']:5.2f} V | {c_b2:5.3f} A | Total Power: {p_b2:.2f} W")


        state = "on " if pins["hdd_3"] == 1 else "off"
        c_b3 = data['c_hdd_b3_12v']
        p_b3 = data['v_hdd_b3_12v'] * c_b3
        if p_b3 > 1:
            p_b3 *= 1.4 ## 5v rail
        print(f"{state} Bundle 3 (12V Only): {data['v_hdd_b3_12v']:5.2f} V | {c_b3:5.3f} A | Total Power: {p_b3:.2f} W")

        if pins["hdd_4"] == 1:
            state = "on "
            p_b4 = 18.0
        else:
            state = "off"
            p_b4 = 0.0
        print(f"{state} Bundle 4 (no telemetry as of now)")

        print("="*55)
        print(f" 🔌 Total Array Estimated Draw: {p_19 + p_33 + p_b1 + p_b2 + p_b3 + p_b4 + p_psu:.2f} Watts\n")

    def power_on_sequence(self, pins):
        """Automates safe cold-boot tracking: ATX -> HBA Switch and PCIe Rescan -> HDD Spin-ups."""

        if pins["pcie_pw"] == 1:
            print("[1/3] HBA is already on")
        else:
            print("[1/3] ", end ="")
            if not self.switch_hba(1):
                print("Aborting powerup")
                return

        if pins["atx_psu"] == 1:
            print("[2/3] ATX psu is already on")
        else:
            print("[2/3] ", end = "")
            self.switch_psu(1)
            time.sleep(1.0)

        if pins["hdd_1"] == 1:
            print("[3.1/3] HDD bundle 1 is already on")
        else:
            print("[3.1/3] ", end="")
            self.switch_hdd(1, 1)

        if pins["hdd_2"] == 1:
            print("[3.2/3] HDD bundle 2 is already on")
        else:
            print("[3.2/3] ", end="")
            self.switch_hdd(1, 2)

        print("\n✅ Subsystem initialized. Run your mount commands or mergerfs mount point targets now.")

    def power_off_sequence(self, pins):
        """Gracefully tears down paths: Drives Off -> unbind drivers and unpower HBA -> PSU Sleep."""

        if pins["hdd_1"] == 0:
            print("[1.1/3] HDD bundle 1 is already off")
        else:
            print("[1.1/3] ", end="")
            self.switch_hdd(0, 1)
            time.sleep(0.5)

        if pins["hdd_2"] == 0:
            print("[3.2/3] HDD bundle 2 is already off")
        else:
            print("[3.2/3] ", end="")
            self.switch_hdd(0, 2)
            time.sleep(0.5)

        if pins["pcie_pw"] == 0:
            print("[2/3] HBA is already off")
        else:
            print("[2/3] ", end ="")
            self.switch_hba(0)

        if pins["atx_psu"] == 0:
            print("[3/3] ATX psu is already off")
        else:
            print("[3/3] ", end = "")
            self.switch_psu(0)
            time.sleep(1.0)

        print("\n🔒 Storage pool completely powered down. Consumption dropped to raw idle baseline.")

    def manual_toggle(self, target, state, pins):
        print(f"toggle {target} to {state}")
        if target == "psu":
            if pins["atx_psu"] == state:
                print("PSU already at this state")
                return

        if target == "hba":
            if pins["pcie_pw"] == state:
                print("HBA already at this state")
                return

        if state == 0 and (target == "psu" or target == "hba"):
            for i in range(4):
                if pins["hdd_" +str(i+1)] == 1:
                    print(f"Hdd bundle {i} is on, can't switch off {target}")
                    return

        if target.startswith("hdd"):
            hdd_id = int(target[-1])
            if pins["hdd_" +str(hdd_id)] == state:
                print(f"HDD{hdd_id} already at this state")
                return

            if pins["atx_psu"] == 0:
                print("Cant turn on hdd without PSU")
                return

            if pins["pcie_pw"] == 0:
                print("Cant turn on hdd without HBA")
                return

        if target == "hba":
            self.switch_hba(state)
        elif target == "psu":
            self.switch_psu(state)
        elif target.startswith("hdd"):
            self.switch_hdd(state, hdd_id)
        else:
            print(f"unknown target >{target}<")

    def switch_hdd(self, state, idx):
        if state == 0:
            print(f"Isolating HDD Bundle Power lines for hdd{idx} (PHD108NQ Gates Closed)...")
            status, res = self.send(f"SWITCH:hdd{idx}:0")
            print(f"Response: Status={status} Data={res}")
        else:
            print(f"Spinning up SAS bundle {idx} (Fast-Sampling Enabled)...")
            status, log1 = self.send(f"SWITCH:hdd{idx}:1")
            print(status)
            print(log1)
            if idx == 1:
                print(f"  -> Bundle 1 Online. Spin-up Peak Amps: 12v:{log1['peak_current_12']:.3f}A 5v:{log1['peak_current_5']:.3f}A")
            else:
                print(f"  -> Bundle {idx} Online. Spin-up Peak Amps: 12v:{log1['peak_current_12']:.3f}A")

    def switch_psu(self, state):
        if state == 0:
            print("Putting ATX Power Supply into sleep state...")
        else:
            print("Energizing main ATX Power Supply...")
        status, res = self.send(f"SWITCH:psu:{state}")
        print(f"Response: Status={status} Data={res}")

    def switch_hba(self, state) -> bool:
        if state == 0:
            print("Unbinding driver from hba in Linux Kernel space...", flush=True)
#            os.system(f"echo '{HBA_PCI_ADDR}' > /sys/bus/pci/drivers/{HBA_DRIVER}/unbind 2>/dev/null")
            os.system(f"echo '{HBA_PCI_ADDR}' > /sys/bus/pci/drivers/{HBA_DRIVER}/unbind")
            print("Removing hba device", flush=True)
#            os.system(f"echo 1 > /sys/bus/pci/devices/{HBA_PCI_ADDR.replace(':', '\\:')}/remove 2>/dev/null")
            os.system(f"echo 1 > /sys/bus/pci/devices/{HBA_PCI_ADDR.replace(':', '\\:')}/remove")
            time.sleep(1.0)
            print("Powering down HBA", flush=True)
            status, res = self.send("SWITCH:hba:0")
        else:
            print("Initializing LSI HBA Link (Sequencing PERST# Interlock)...", flush=True)
            status, res = self.send("SWITCH:hba:1")
            if status == "ERROR":
                print(f"❌ Aborting sequence: {res['msg']}")
                return False
            print("Disabling D3 cold states...", flush=True)
#            os.system(f"echo 0 > /sys/bus/pci/devices/{BRIDGE_PCI_ADDR.replace(':', '\\:')}/d3cold_allowed 2>/dev/null")
            os.system(f"echo 0 > /sys/bus/pci/devices/{BRIDGE_PCI_ADDR.replace(':', '\\:')}/d3cold_allowed")
            print("Triggering Kernel PCI Bus Rescan to discover LSI 2308...", flush=True)
            time.sleep(2.0) # hba startup
            os.system("echo 1 > /sys/bus/pci/rescan")
            time.sleep(2.0) # Buffer window for mpt3sas kernel initialization modules to mount
            print("Enabling D3 cold states...", flush=True)
#            os.system(f"echo 1 > /sys/bus/pci/devices/{BRIDGE_PCI_ADDR.replace(':', '\\:')}/d3cold_allowed 2>/dev/null")
            os.system(f"echo 1 > /sys/bus/pci/devices/{BRIDGE_PCI_ADDR.replace(':', '\\:')}/d3cold_allowed")

        print(f"Response: Status={status} Data={res}")
        return True




# --- ARGUMENT ROUTER ---
if __name__ == '__main__':
    # Force execution via sudo since modifying /sys/bus/pci paths requires root privileges
    if os.geteuid() != 0 and ("--status" not in sys.argv and "-s" not in sys.argv):
        print("❌ Power state changes modify PCIe infrastructure. Please run this script with 'sudo'.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Master Command Center for DIY Storage-on-Demand Cluster")
    parser.add_argument('-s', '--status', action='store_true', help='Read live voltages, currents, and wattages.')
    parser.add_argument('--up', action='store_true', help='Execute complete safe automated power-up sequence.')
    parser.add_argument('--down', action='store_true', help='Execute safe unbind and complete hardware shutdown sequence.')
    parser.add_argument("-t", '--toggle', nargs=2, metavar=('TARGET', 'STATE'), help='Manual hardware override. Ex: --toggle hdd1 1')

    args = parser.parse_args()
    cli = StorageCLI()

    if args.status:
        pins = cli.status_pins()
        cli.status_va(pins)

    elif args.up:
        pins = cli.status_pins()
        cli.power_on_sequence(pins)
    elif args.down:
        pins = cli.status_pins()
        cli.power_off_sequence(pins)
    elif args.toggle:
        target = args.toggle[0]
        state = int(args.toggle[1])
        if state == 0 or state == 1:
            pins = cli.status_pins()
            cli.manual_toggle(target, state, pins)
        else:
            print("Bad state, 0 = off, 1 = on, no other options")
    else:
        parser.print_help()
