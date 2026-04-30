const filleted_box = `from build123d import *

result = Box(50, 30, 10)
result = fillet(result.edges(), radius=2)`;

const plate_with_hole = `from build123d import *

length = 80.0
height = 60.0
thickness = 10.0
center_hole_dia = 22.0

result = Box(length, height, thickness) - Cylinder(center_hole_dia / 2, thickness)`;

export const models = {
  'default': filleted_box,
  'plate_with_hole': plate_with_hole
}
