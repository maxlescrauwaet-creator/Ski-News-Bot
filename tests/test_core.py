import datetime as dt
from skibot import content, images, news


def test_license_filter():
    assert images.license_ok("CC BY 4.0", False)
    assert images.license_ok("CC0", False)
    assert images.license_ok("Public domain", False)
    assert not images.license_ok("CC BY-SA 4.0", False) and images.license_ok("CC BY-SA 4.0", True)
    assert not images.license_ok("CC BY-NC 2.0", True)
    assert not images.license_ok("CC BY-ND 2.0", True)
    assert not images.license_ok("", True)


def test_grounding():
    src = "Resort adds 40 snow guns in Zermatt."
    assert content.grounded("The resort adds 40 snow guns.", src)
    assert not content.grounded("The resort adds 45 snow guns.", src)
    assert not content.grounded("Resort adds snow guns, says Dupont.", src)


def test_similarity():
    assert news.similar("Zermatt opens new gondola", "Zermatt opens a new gondola!")
    assert not news.similar("Zermatt opens new gondola", "Niseko records heavy snowfall")
