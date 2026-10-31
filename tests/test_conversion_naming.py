import pytest
from pydantic import ValidationError
from mediaseed.conversion import ConversionChoice
from mediaseed.conversion_naming import converted_path, converted_stem


def test_remux_and_previous_codec_and_group_are_replaced_but_title_episode_and_audio_survive():
    old='Fast.and.Furious.7.2015.NORDIC.TC.REMUX.2160p.DV.HDR.HYBRID.UHD-BluRay.HEVC.DTS-HD.HRA.7.1-RAPiDCOWS'
    name=converted_stem(old,30,'MDTech')
    assert name=='Fast.and.Furious.7.2015.NORDIC.TC.2160p.DTS-HD.HRA.7.1.H265.30Mbps-MDTech'
    assert converted_stem('The.Remux.2020.1080p.BluRay.x264-OldGroup',20,'MDTech')=='The.Remux.2020.1080p.H265.20Mbps-MDTech'
    assert converted_stem('Show.S01E02.1080p.BluRay.H265-Old',40,'MyTag')=='Show.S01E02.1080p.H265.40Mbps-MyTag'


def test_nested_release_folders_also_get_correct_tag_and_mkv_extension():
    name=converted_path('Movie.2020.2160p.BluRay.REMUX-Old/Movie.2020.2160p.BluRay.REMUX-Old.mp4',30,'MDTech')
    assert name=='Movie.2020.2160p.H265.30Mbps-MDTech.mkv'
    assert converted_path('Season 01/Episode.S01E01.mkv',20,'MDTech')=='Season 01/Episode.S01E01.H265.20Mbps-MDTech.mkv'


@pytest.mark.parametrize('tag',['','../escape','tag/escape','tag\\escape','a'*33,'bad tag'])
def test_invalid_release_tag_fails_before_job_can_be_created(tag):
    with pytest.raises(ValidationError):
        ConversionChoice(torrent_hash='a'*40,indexes=[0],location='local',release_tag=tag)


def test_long_unicode_title_stays_within_filesystem_component_limit():
    name=converted_stem('Æ'*300,30,'MDTech')+'.mkv'
    assert len(name.encode())<=240 and name.endswith('.H265.30Mbps-MDTech.mkv')


def test_windows_movie_path_avoids_repeating_release_title():
    from mediaseed.conversion_naming import library_label
    old='The.Conjuring.Last.Rites.2025.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HDR10P.HEVC.HYBRID.REMUX-FraMeSToR'
    name=converted_path(old+'/'+old+'.mkv',20,'MDTech')
    assert '/' not in name
    folder=library_label(old)+' [220f98f2]'
    assert folder=='The.Conjuring.Last.Rites.2025 [220f98f2]'
    assert len('Z:/media/Konverterede Film/Konverteret/'+folder+'/'+name)<240
    assert '/' not in library_label('../bad/name')


def test_single_movie_folder_matches_filename_and_batch_stays_distinct():
    from mediaseed.conversion_naming import library_folder
    files=[{'name':'Source.mkv','output':'Film.2025.2160p.H265.20Mbps-MDTech.mkv'}]
    assert library_folder(files,'Original REMUX','a'*32)=='Film.2025.2160p.H265.20Mbps-MDTech'
    assert library_folder(files*2,'Show.2025','a'*32)=='Show.2025 [aaaaaaaa]'
