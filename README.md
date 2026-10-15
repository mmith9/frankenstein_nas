This is a custom build nas controls.

The setup consists of:
Main unit: Dell Wyse 5070 thin client with added pcie express port.
Sas controller: some clone of Lsi 2308 HBA in IT mode.
ESP32 to provide interface between custom circuits and thin client.

The frankenstein NAS is built for low power consumption, and has ability to turn unused hardware off, including all hdds, atx psu and can poweroff pcie slot aswell.

Files:
main.py - this is micropython code that goes to ESP32

nas.py - this is CLI control of the nas subsystems invoked on main unit.

Intel's pdf about Gemini Lake SoC:
https://www.intel.com/content/www/us/en/content-details/336560/intel-pentium-silver-and-celeron-processors-datasheet-vol-1.html
Contains a map of balls of fcbga1090 Soc. The format of the map in the pdf is absolutely atrocious. I've made a little toolsie that will generate a kicad footprint of the SoC that's actually useful.

