import sys
from machine import I2C, Pin
import time

class INA3221_Node:
    def __init__(self, i2c, address=0x40, shunt_resistor=0.01):
        self.i2c = i2c
        self.addr = address
        self.shunt = shunt_resistor
        self._REG_CONFIG = 0x00
        self._REG_SHUNTV = {1: 0x01, 2: 0x03, 3: 0x05}
        self._REG_BUSV = {1: 0x02, 2: 0x04, 3: 0x06}
        self.set_mode_normal()
        
    def _write_config(self, val):
        data = bytes([(val >> 8) & 0xFF, val & 0xFF])
        self.i2c.writeto_mem(self.addr, self._REG_CONFIG, data)

    def set_mode_normal(self):
        """Standard monitoring with 4x sample averaging."""
        self._write_config(0x7127 | (0b001 << 9))

    def set_mode_fast(self):
        """Ultra-fast 144us sampling with NO averaging to catch spin-up spikes."""
        self._write_config(0x7127)

    def _read_register(self, reg):
        try:
            raw = self.i2c.readfrom_mem(self.addr, reg, 2)
            value = (raw[0] << 8) | raw[1]
            return value - 0x10000 if value & 0x8000 else value
        except: return 0

    def get_bus_voltage(self, channel):
        return (self._read_register(self._REG_BUSV[channel]) >> 3) * 0.008

    def get_current_a(self, channel):
        raw = self._read_register(self._REG_SHUNTV[channel])
        return (((raw >> 3) * 0.040) / 1000.0) / self.shunt


# --- 🛠️ 0 off 1 on pins ---
atx_psu  = Pin(4,  Pin.OUT, value=0)         # 0 = ATX PSU OFF (2N7002 gate LOW)
pcie_pw  = Pin(5,  Pin.OUT, value=1)         # 1 = HBA on, required for boot

# --- 🛠️ reversed logic pins 1 off 0 on pins ---
perst_n = Pin(16, Pin.OPEN_DRAIN, value=1)  # 1 = Passive / Transparent (Reset released)

# HDD 1-4 bundles
# 1 = Keeps 2N7002 ON, forcing PHD108NQ gates to 0V (Drives safely OFF at boot)
hdd_pins = [
    Pin(13, Pin.OUT, value=1), # Bundle 1 
    Pin(14, Pin.OUT, value=1), # Bundle 2 
    Pin(15, Pin.OUT, value=1), # Bundle 3
    Pin(17, Pin.OUT, value=1)  # Bundle 4
]

# --- I2C INITIALIZATION ---
i2c_bus = I2C(0, sda=Pin(21), scl=Pin(22), freq=400000)
ina_1 = INA3221_Node(i2c_bus, address=0x40, shunt_resistor=0.01) # Mod 1 (Host/PCIe side)
ina_2 = INA3221_Node(i2c_bus, address=0x41, shunt_resistor=0.01) # Mod 2 (Drive telemetry side)

def send_response(status, data=None):
    if data is None: data = {}
    print(f"__RESP__:{status}:{data}")


# --- HOST INTERACTION CONTROLLERS ---

def report_states():
    payload = {
        "atx_psu":atx_psu.value(),
        "pcie_pw":pcie_pw.value(),
        "perst_n":1 - perst_n.value(),
    }
    for i in range(4):
        payload["hdd_" + str(i+1)] = 1 - hdd_pins[i].value()
    send_response("OK", payload)

def report_va():
    payload = {
        # Module 1 (0x40): Host Control & Motherboard rails
        "v_19_5v_brick": ina_1.get_bus_voltage(1),
        "c_19_5v_brick": ina_1.get_current_a(1),
               
        "v_hba_3_3v":    ina_1.get_bus_voltage(2),
        "c_hba_3_3v":    ina_1.get_current_a(2),
        
        # Module 2 (0x41): Completely dedicated to the HDD array side
        "v_hdd_b1_12v":  ina_2.get_bus_voltage(1),
        "c_hdd_b1_12v":  ina_2.get_current_a(1),
        "v_hdd_b1_5v":   ina_2.get_bus_voltage(2),
        "c_hdd_b1_5v":   ina_2.get_current_a(2),
        "v_hdd_b2_12v":  ina_2.get_bus_voltage(3),  
        "c_hdd_b2_12v":  ina_2.get_current_a(3),
        "v_hdd_b3_12v":  ina_1.get_bus_voltage(3),  
        "c_hdd_b3_12v":  ina_1.get_current_a(3),
        "v_hdd_b4_12v":  0.0,  
        "c_hdd_b4_12v":  0.0,
    }
    send_response("OK", payload)

def switch(target:str, state:int):
    if target == "psu": 
        atx_psu.value(state)
        time.sleep_ms(100)
        send_response("OK", {"target": target, "state": state})

    elif target == "hba":
        hba_transition(state)

    elif target == "perst": 
        perst_n.value(0 if state == 0 else 1)
        send_response("OK", {"target": target, "state": state})
        time.sleep_ms(10)

    elif target.startswith("hdd"):
        idx = int(target.replace("hdd", ""))
        transition_hdd(idx, state)

def hba_transition(state:int):
    perst_n.value(0) # Clamp PERST# to GND before any voltage changes
    time.sleep_ms(50) 
   
    if state == 1:
        ina = ina_1
        ina.set_mode_fast()
        peak_33v_current = 0.0
        peak_195v_current = 0.0

        pcie_pw.value(1)
        stable = False
        timeout = time.ticks_add(time.ticks_ms(), 3000)
        while time.ticks_diff(timeout, time.ticks_ms()) > 0:
            # if ina_1.get_bus_voltage(2) >= 3.1:
            #     stable = True
            #     break
            c195 = ina.get_current_a(1)
            if c195 > peak_195v_current:
                peak_195v_current = c195
            c33 = ina.get_current_a(2)
            if c33 > peak_33v_current:
                peak_33v_current = c33

            time.sleep_ms(10)

        ina.set_mode_normal()
        stable = ina.get_bus_voltage(2) >= 3.0   

        if stable:
            send_response("OK", {"target": "hba", "state": 1, "peak_current_195": peak_195v_current, "peak_current_33":peak_33v_current})
        else:
            pcie_pw.value(0)    # Safety backup isolate
            send_response("ERROR", {"msg": "HBA power rails failed to stabilize!", "peak_current_195": peak_195v_current, "peak_current_33":peak_33v_current})
    else:
        pcie_pw.value(0)
        send_response("OK", {"target": "hba", "state": 0})

    time.sleep_ms(150)
    perst_n.value(1)        


def transition_hdd(idx:int, state:int):
    """Spins up a drive bundle and catches the transient motor spike using Mod 2."""
    
    if state == 0:
        hdd_pins[idx-1].value(1) # inverted logic
        time.sleep_ms(100)
        send_response("OK", {"bundle": idx, state:0})        
        return

    end_time = time.ticks_add(time.ticks_ms(), 5000)
    peak_12v_current = 0.0

    if idx == 1:
        peak_5v_current = 0.0
        ina = ina_2
        ina.set_mode_fast()
        hdd_pins[0].value(0) 
        while time.ticks_diff(end_time, time.ticks_ms()) > 0:
            c12 = ina.get_current_a(1)
            if c12 > peak_12v_current:
                peak_12v_current = c12
            c5 = ina.get_current_a(2)
            if c5 > peak_5v_current:
                peak_5v_current = c5
        send_response("TRANSITION_LOG", {"bundle": idx, "peak_current_12": peak_12v_current, "peak_current_5":peak_5v_current})

    else:
        if idx == 2:
            ina = ina_2
            ch = 3
        elif idx == 3:
            ina = ina_1
            ch = 3
        elif idx == 4:
            hdd_pins[idx-1].value(0) # Drop pin to 0V -> Turns 2N7002 OFF -> Activates PHD108NQ        
            time.sleep_ms(5000)
            send_response("TRANSITION_LOG", {"bundle": idx, "peak_current_12": 0.0})

        else:
            raise BaseException("no such hdd bundle")

        ina.set_mode_fast()
        hdd_pins[idx-1].value(0) # Drop pin to 0V -> Turns 2N7002 OFF -> Activates PHD108NQ        
        while time.ticks_diff(end_time, time.ticks_ms()) > 0:
            c = ina.get_current_a(ch)
            if c > peak_12v_current:
                peak_12v_current = c
        ina.set_mode_normal()

        send_response("TRANSITION_LOG", {"bundle": idx, "peak_current_12": peak_12v_current})


# --- MAIN RUNTIME SERIAL LOOP ---
while True:
    line = sys.stdin.readline().strip()
    
    if not line:
        continue

    try:
        parts = line.split(":")
        cmd = parts[0]
        
        if cmd == "POLL_VA":
            report_va()

        elif cmd == "POLL_STATES":
            report_states()

        elif cmd == "SWITCH":
            target = parts[1]
            state = int(parts[2])
            if state == 1 or state == 0:
                switch(target, state)
            else:
                raise BaseException(f"Bad state {state} only 0 = off, 1 = on allowed")

        else:
            txt = "Unknown cmd >" + str(cmd) + "<"
            send_response("ERROR", {"msg": txt})
    except Exception as e:
        send_response("ERROR", {"msg": str(e)})

