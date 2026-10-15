# =====================================================================
# THE PDF:
# https://www.intel.com/content/www/us/en/content-details/336560/intel-pentium-silver-and-celeron-processors-datasheet-vol-1.html
# =====================================================================

the_pdf = """
# ctrl-a, ctrl-c and ctrl-v here

"""

PAD_SIZE_MM = 0.45
OUTPUT_FILENAME = "FCBGA1090_Custom.kicad_mod"

def build_kicad_footprint():
    target = 0
    signal_map = {}
    pad_expressions = []

    for line in the_pdf.split("\n"):
        line_split = line.strip().split()
        if len(line_split) != 3:
            continue

        if line_split == ["Ball#", "DDR4", "LPDDR4"]: 
            target = 1
            continue

        if line_split == ["Pin", "X", "Y"]:
            target = 2
            continue

        if target == 0:
            continue

        if line_split == ["6", "Package", "Information"]:
            break

        if target == 1:
            ball_num = line_split[0]
            ddr4_name = line_split[1]
            signal_map[ball_num] = ddr4_name
            continue

        if target == 2:
            pin_label = line_split[0]
            try:
                x_coord = float(line_split[1])
                y_coord = float(line_split[2])
            except ValueError:
                continue

            if pin_label not in signal_map:
                print(f"Warning: unmatched pin label: {pin_label}")
                display_name = pin_label
            else:
                display_name = signal_map.get(pin_label)
            pad_str = f'  (pad "{display_name}" smd circle (at {x_coord:.4f} {y_coord:.4f}) (size {PAD_SIZE_MM} {PAD_SIZE_MM}) (layers "F.Cu" "F.Paste" "F.Mask"))'
            pad_expressions.append(pad_str)

    footprint_template = f"""(footprint "FCBGA1090_Custom"
  (version 20240108)
  (generator "Custom_Parser")
  (layer "F.Cu")
  (descr "FCBGA 1090-pin custom layout mapped to DDR4 signal names, 24x25mm")
  (tags "BGA SoC Intel Wyse 5070 Reverse Engineering")
  (attr smd)
  (fp_text reference "REF**" (at 0 -14.0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
  (fp_text value "FCBGA1090_Custom" (at 0 14.0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
  (fp_rect (start -12.00 -12.50) (end 12.00 12.50) (stroke (width 0.12) (type solid)) (layer "F.Fab"))
  (fp_rect (start -12.25 -12.75) (end 12.25 12.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -11.00 -12.50) (end -12.00 -11.50) (stroke (width 0.12) (type solid)) (layer "F.Fab"))
{chr(10).join(pad_expressions)}
)"""

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(footprint_template.strip())

    
    print(f"[Success] The program did not crash")
    if len(pad_expressions) == 1090:
        print(f"[Success] Translated and generated 1090, exactly as expected")
    else:
        print(f"[Fail] Expected 1090 pads, actually translated: {len(pad_expressions)}")
    print(f"[Success] Footprint file saved as: '{OUTPUT_FILENAME}'")

if __name__ == "__main__":
    build_kicad_footprint()
