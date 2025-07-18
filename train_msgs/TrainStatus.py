"""LCM type definitions
This file automatically generated by lcm.
DO NOT MODIFY BY HAND!!!!
"""


from io import BytesIO
import struct

class TrainStatus(object):

    __slots__ = ["timestamp", "epoch", "step", "loss", "reward", "lr"]

    __typenames__ = ["int64_t", "int32_t", "int32_t", "double", "double", "double"]

    __dimensions__ = [None, None, None, None, None, None]

    def __init__(self):
        self.timestamp = 0
        """ LCM Type: int64_t """
        self.epoch = 0
        """ LCM Type: int32_t """
        self.step = 0
        """ LCM Type: int32_t """
        self.loss = 0.0
        """ LCM Type: double """
        self.reward = 0.0
        """ LCM Type: double """
        self.lr = 0.0
        """ LCM Type: double """

    def encode(self):
        buf = BytesIO()
        buf.write(TrainStatus._get_packed_fingerprint())
        self._encode_one(buf)
        return buf.getvalue()

    def _encode_one(self, buf):
        buf.write(struct.pack(">qiiddd", self.timestamp, self.epoch, self.step, self.loss, self.reward, self.lr))

    @staticmethod
    def decode(data: bytes):
        if hasattr(data, 'read'):
            buf = data
        else:
            buf = BytesIO(data)
        if buf.read(8) != TrainStatus._get_packed_fingerprint():
            raise ValueError("Decode error")
        return TrainStatus._decode_one(buf)

    @staticmethod
    def _decode_one(buf):
        self = TrainStatus()
        self.timestamp, self.epoch, self.step, self.loss, self.reward, self.lr = struct.unpack(">qiiddd", buf.read(40))
        return self

    @staticmethod
    def _get_hash_recursive(parents):
        if TrainStatus in parents: return 0
        tmphash = (0x63c49744d2078499) & 0xffffffffffffffff
        tmphash  = (((tmphash<<1)&0xffffffffffffffff) + (tmphash>>63)) & 0xffffffffffffffff
        return tmphash
    _packed_fingerprint = None

    @staticmethod
    def _get_packed_fingerprint():
        if TrainStatus._packed_fingerprint is None:
            TrainStatus._packed_fingerprint = struct.pack(">Q", TrainStatus._get_hash_recursive([]))
        return TrainStatus._packed_fingerprint

    def get_hash(self):
        """Get the LCM hash of the struct"""
        return struct.unpack(">Q", TrainStatus._get_packed_fingerprint())[0]

