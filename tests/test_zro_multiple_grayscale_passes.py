import pytest
from datetime import datetime, timedelta
from calibrator.zro_import import parse_zro_csv, ZROImportResult, merge_into_session

def create_zro_row(index: int, r: int, g: int, b: int, Y: float, x: float, y: float, timestamp: datetime):
    return f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}\t{r}\t{g}\t{b}\t{Y}\t{x}\t{y}\t0\n"

def create_grayscale_ramp(start_time: datetime, offset_seconds: int = 0):
    rows = []
    ts = start_time + timedelta(seconds=offset_seconds)
    # Simple ramp: 0%, 50%, 100%
    rows.append(create_zro_row(0, 16, 16, 16, 0.1, 0.31, 0.32, ts))
    rows.append(create_zro_row(1, 126, 126, 126, 50.0, 0.31, 0.32, ts + timedelta(seconds=1)))
    rows.append(create_zro_row(2, 235, 235, 235, 100.0, 0.31, 0.32, ts + timedelta(seconds=2)))
    return "".join(rows)

def test_zro_multiple_grayscale_passes():
    start_time = datetime(2026, 4, 16, 12, 0, 0)
    
    # Pass 1: Pre (should go to pre_measurements)
    ramp1 = create_grayscale_ramp(start_time, 0)
    # Pass 2: Mid (should go to mid_measurements)
    ramp2 = create_grayscale_ramp(start_time, 300) # 5 min gap
    # Pass 3: Post (should go to post_measurements)
    ramp3 = create_grayscale_ramp(start_time, 600) # 5 min gap
    
    csv_content = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n" + ramp1 + ramp2 + ramp3
    
    result = parse_zro_csv(csv_content)
    
    assert result.grayscale_sessions == 3
    assert len(result.grayscale_passes) == 3
    
    # Check bucketing
    assert len(result.pre_measurements) == 3
    assert len(result.mid_measurements) == 3
    assert len(result.post_measurements) == 3
    
    # Verify contents (using Y value as a simple differentiator if we changed them, 
    # but here they are same, so we check timestamps)
    assert result.pre_measurements[0]['timestamp'] == start_time.isoformat()
    assert result.mid_measurements[0]['timestamp'] == (start_time + timedelta(seconds=300)).isoformat()
    assert result.post_measurements[0]['timestamp'] == (start_time + timedelta(seconds=600)).isoformat()

def test_zro_two_grayscale_passes():
    start_time = datetime(2026, 4, 16, 12, 0, 0)
    ramp1 = create_grayscale_ramp(start_time, 0)
    ramp2 = create_grayscale_ramp(start_time, 300)
    
    csv_content = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n" + ramp1 + ramp2
    result = parse_zro_csv(csv_content)
    
    assert result.grayscale_sessions == 2
    assert len(result.pre_measurements) == 3
    assert len(result.post_measurements) == 3
    assert len(result.mid_measurements) == 0

def test_zro_single_grayscale_pass():
    start_time = datetime(2026, 4, 16, 12, 0, 0)
    ramp1 = create_grayscale_ramp(start_time, 0)
    
    csv_content = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n" + ramp1
    result = parse_zro_csv(csv_content)
    
    assert result.grayscale_sessions == 1
    assert len(result.pre_measurements) == 3
    assert len(result.post_measurements) == 0
    assert len(result.mid_measurements) == 0


def test_zro_merge_with_mid_passes():
    start_time = datetime(2026, 4, 16, 12, 0, 0)
    # 3 ramps: pre, mid, post
    ramp1 = create_grayscale_ramp(start_time, 0)
    ramp2 = create_grayscale_ramp(start_time, 300)  # 5 min gap
    ramp3 = create_grayscale_ramp(start_time, 600)  # 5 min gap
    csv_content = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n" + ramp1 + ramp2 + ramp3
    
    result = parse_zro_csv(csv_content)
    session = {}
    merge_into_session(session, result)
    
    import_meta = session["zro_imports"][0]
    assert "mid_passes" in import_meta
    assert len(import_meta["mid_passes"]) == 1
    # Check that the mid pass is indeed the second ramp
    assert import_meta["mid_passes"][0][0]['timestamp'] == (start_time + timedelta(seconds=300)).isoformat()


def test_zro_merge_without_mid_passes():
    start_time = datetime(2026, 4, 16, 12, 0, 0)
    # 2 ramps: pre, post
    ramp1 = create_grayscale_ramp(start_time, 0)
    ramp2 = create_grayscale_ramp(start_time, 300)  # 5 min gap
    csv_content = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n" + ramp1 + ramp2
    
    result = parse_zro_csv(csv_content)
    session = {}
    merge_into_session(session, result)
    
    import_meta = session["zro_imports"][0]
    assert "mid_passes" in import_meta
    assert len(import_meta["mid_passes"]) == 0
