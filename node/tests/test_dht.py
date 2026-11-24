"""DHT11 frame decoding, from synthetic edge timings (no Pi needed)."""
import unittest

from gardening_node import dht


def _edges_for(frame):
    """Edges (level, ns) as the sensor sends them: response pulse, then 40 bits."""
    edges, t = [], 0
    edges += [(1, t), (0, t + 80_000)]          # response: 80 µs high
    t += 80_000
    for byte in frame:
        for i in range(7, -1, -1):
            t += 50_000                         # 50 µs low before every bit
            width = 70_000 if byte >> i & 1 else 27_000
            edges += [(1, t), (0, t + width)]
            t += width
    return edges


def _with_checksum(data):
    return [*data, sum(data) & 0xFF]


class DecodeTest(unittest.TestCase):
    def test_frame_seen_on_the_node(self):
        # 24 °C / 34 % as read on node-in-beijing
        self.assertEqual(dht.decode([34, 0, 24, 0, 58]), (24.0, 34.0))

    def test_temperature_tenths(self):
        self.assertEqual(dht.decode(_with_checksum([45, 0, 23, 7])), (23.7, 45.0))

    def test_negative_temperature(self):
        # Bit 7 of the tenths byte: -1.5 °C
        self.assertEqual(dht.decode(_with_checksum([80, 0, 1, 0x85])), (-1.5, 80.0))

    def test_out_of_range_reading_is_rejected(self):
        # 150 % humidity passes the checksum but must not be published
        with self.assertRaises(dht.DHTError):
            dht.decode(_with_checksum([150, 0, 24, 0]))


class FrameFromEdgesTest(unittest.TestCase):
    def test_pulse_widths_become_the_sent_bytes(self):
        frame = _with_checksum([34, 0, 24, 0])
        self.assertEqual(dht.frame_from_edges(_edges_for(frame)), frame)

    def test_corrupted_frame_fails_the_checksum(self):
        with self.assertRaises(dht.DHTError):
            dht.frame_from_edges(_edges_for([34, 0, 24, 0, 59]))

    def test_truncated_response_is_an_error(self):
        with self.assertRaises(dht.DHTError):
            dht.frame_from_edges(_edges_for(_with_checksum([34, 0, 24, 0]))[:-10])


if __name__ == "__main__":
    unittest.main()
