/**
 * Where each printed row sits on a generated slip, in the 760 x 1000 px
 * layout of data/gen_slips.py: [top, height]. One constant, so the highlight
 * and the loupe follow the generator if it changes.
 */
export const SLIP_SIZE = { width: 760, height: 1000 }
export const SLIP_ROWS: Record<string, [number, number]> = {
  ticketNo: [232, 44],
  vehicleNo: [324, 44],
  gross: [500, 44],
  tare: [546, 44],
  net: [592, 58],
  timeIn: [694, 44],
  timeOut: [740, 44],
}
// The loupe shows the disputed row with its neighbour, for context.
export const LOUPE: Record<string, [number, number]> = {
  timeIn: [684, 104],
  net: [496, 160],
  vehicleNo: [314, 64],
}

