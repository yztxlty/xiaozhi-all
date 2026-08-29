from __future__ import annotations

import ctypes
import ctypes.util
import os


_SAMPLE_RATE = 16000
_CHANNELS = 1
_FRAME_SAMPLES = 960
_MAX_PACKET = 4096


def _library() -> ctypes.CDLL:
    path = os.getenv("XIAOZHI_LIBOPUS") or ctypes.util.find_library("opus") or "/opt/homebrew/opt/opus/lib/libopus.dylib"
    try:
        return ctypes.CDLL(path)
    except OSError as exc:
        raise RuntimeError("未找到 libopus，请安装系统 Opus 编解码库") from exc


class OpusDecoder:
    def __init__(self) -> None:
        lib = _library()
        lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
        lib.opus_decode.restype = ctypes.c_int
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib = lib
        error = ctypes.c_int()
        self._handle = lib.opus_decoder_create(_SAMPLE_RATE, _CHANNELS, ctypes.byref(error))
        if not self._handle or error.value != 0:
            raise RuntimeError(f"Opus 解码器创建失败：{error.value}")

    def decode(self, frame: bytes) -> bytes:
        if not frame:
            raise ValueError("Opus 帧不能为空")
        output = (ctypes.c_int16 * (_FRAME_SAMPLES * 2))()
        data = ctypes.create_string_buffer(frame)
        samples = self._lib.opus_decode(self._handle, data, len(frame), output, _FRAME_SAMPLES * 2, 0)
        if samples < 0:
            raise ValueError(f"Opus 解码失败：{samples}")
        return ctypes.string_at(output, samples * _CHANNELS * 2)

    def close(self) -> None:
        if self._handle:
            self._lib.opus_decoder_destroy(self._handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()


class OpusEncoder:
    def __init__(self, sample_rate: int = _SAMPLE_RATE, frame_samples: int | None = None) -> None:
        lib = _library()
        lib.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.opus_encode.restype = ctypes.c_int
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib = lib
        error = ctypes.c_int()
        self._sample_rate = sample_rate
        self._frame_samples = frame_samples or sample_rate * 60 // 1000
        self._handle = lib.opus_encoder_create(sample_rate, _CHANNELS, 2049, ctypes.byref(error))
        if not self._handle or error.value != 0:
            raise RuntimeError(f"Opus 编码器创建失败：{error.value}")

    def encode(self, pcm: bytes) -> bytes:
        if len(pcm) != self._frame_samples * 2:
            raise ValueError(f"PCM 帧必须包含 {self._frame_samples} 个采样")
        samples = (ctypes.c_int16 * self._frame_samples).from_buffer_copy(pcm)
        output = ctypes.create_string_buffer(_MAX_PACKET)
        size = self._lib.opus_encode(self._handle, samples, self._frame_samples, output, _MAX_PACKET)
        if size < 0:
            raise ValueError(f"Opus 编码失败：{size}")
        return output.raw[:size]

    def close(self) -> None:
        if self._handle:
            self._lib.opus_encoder_destroy(self._handle)
            self._handle = None

    def __del__(self) -> None:
        self.close()
