from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from mediaseed.conversion_decoder import capabilities,select_decoder


@pytest.fixture
def executable(tmp_path):
    path=tmp_path/'nvencc';path.write_text('synthetic')
    capabilities.cache_clear()
    return str(path)


def test_nvdec_section_is_required_and_encoder_support_is_not_enough(executable,monkeypatch):
    monkeypatch.setattr('mediaseed.conversion_decoder.subprocess.run',lambda *a,**kw:Mock(stdout='NVEnc features\nCodec: H.265/HEVC\n10bit depth yes\nNVDec features\n H.264/AVC: nv12, yv12\n H.265/HEVC: nv12, yv12\n'))
    assert select_decoder({'codec_name':'hevc','pix_fmt':'yuv420p10le'},executable)=='avsw'
    assert select_decoder({'codec_name':'h264','pix_fmt':'yuv420p'},executable)=='avhw'


def test_supported_ten_bit_hevc_uses_gpu_and_capability_probe_is_cached(executable,monkeypatch):
    calls=[]
    monkeypatch.setattr('mediaseed.conversion_decoder.subprocess.run',lambda *a,**kw:calls.append(a) or Mock(stdout='NVDec features\n H.265/HEVC: nv12, yv12, yv12(10bit)\n'))
    stream={'codec_name':'hevc','pix_fmt':'yuv420p10le'}
    assert select_decoder(stream,executable)=='avhw'
    assert select_decoder(stream,executable)=='avhw' and len(calls)==1


def test_non_nvdec_formats_keep_software_decode_without_needing_gpu_probe():
    assert select_decoder({'codec_name':'ffv1','pix_fmt':'yuv420p'},'/missing')=='avsw'
    assert select_decoder({'codec_name':'hevc','pix_fmt':'yuv444p10le'},'/missing')=='avsw'


def test_driver_probe_failure_is_actionable_and_does_not_silently_change_policy(executable,monkeypatch):
    def fail(*a,**kw):raise subprocess.TimeoutExpired(a[0],20)
    monkeypatch.setattr('mediaseed.conversion_decoder.subprocess.run',fail)
    with pytest.raises(ValueError,match='decoder-driver'):
        select_decoder({'codec_name':'hevc','pix_fmt':'yuv420p10le'},executable)
