# Fix: Handle color-only analysis without grayscale data

## Problem
When analyzing measurement data that contains only color patches (no grayscale), the `analyze()` function in `calcore/analysis.py` would pass `measured_peak_y=None` to `target_xyz_for_patch()`. This caused two issues:

1. **TypeError**: In HDR mode or PQ EOTF mode, the code would try to perform arithmetic operations with `None` values, causing crashes
2. **Incorrect calculations**: Even if no crash occurred, the fallback logic was not properly implemented, leading to targets being calculated with incorrect peak luminance values

## Solution
Modified `calcore/analysis.py` to implement proper fallback logic:

1. **Added safe default peak luminance**: Define `peak_fallback = 100.0 if cfg.mode.lower() == 'sdr' else 1000.0` 
2. **Implemented effective peak value**: `measured_peak_y_effective = measured_peak_y if measured_peak_y and measured_peak_y > 0 else peak_fallback`
3. **Updated all function calls**: Pass `measured_peak_y_effective` to `target_xyz_for_patch()` instead of `measured_peak_y`
4. **Fixed condition checks**: Updated all conditional checks to use `measured_peak_y_effective > 0` instead of `measured_peak_y > 0`
5. **Added meta flag**: Include `"peak_fallback_used": measured_peak_y is None` in the summary metadata

## Testing
Added `tests/test_analyze_no_grayscale.py` with:
- Test for color-only patches in SDR mode (no exceptions, peak_fallback_used=True)
- Test for color-only patches in HDR mode (no exceptions, peak_fallback_used=True)

## Files Changed
- `calcore/analysis.py`: Implemented fallback logic and updated function calls
- `tests/test_analyze_no_grayscale.py`: Added new test cases

## Verification
All existing tests pass (40/40). New functionality tested with manual verification in both SDR and HDR modes.