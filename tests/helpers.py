def hourly(levels_by_hour):
    """24 hourly rows; hours not given are dark."""
    rows = [[h, 0, 0, 0, 0, 0, 0, 0] for h in range(24)]
    for h, lv in levels_by_hour.items():
        rows[h][2:] = list(lv)
    return rows


def sample_schedule():
    """09:00-21:00 photoperiod, peak 11-18."""
    day = {9: [0, 10, 0, 3, 10, 0], 10: [1, 30, 20, 5, 30, 0],
           19: [1, 30, 20, 5, 30, 0], 20: [0, 10, 0, 3, 10, 0]}
    day.update({h: [5, 80, 50, 10, 80, 0] for h in range(11, 19)})
    return hourly(day)
