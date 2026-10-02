import gzip

import pytest

from horu import dictionary
from horu.nlp import analyze

JMDICT_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE JMdict [
<!ENTITY v5r "Godan verb with 'ru' ending">
<!ENTITY v5k-s "Godan verb - Iku/Yuku special class">
<!ENTITY vi "intransitive verb">
<!ENTITY n "noun (common) (futsuumeishi)">
<!ENTITY num "numeric">
<!ENTITY uk "word usually written using kana alone">
]>
<JMdict>
<entry><ent_seq>1</ent_seq>
<k_ele><keb>成る</keb><ke_pri>ichi1</ke_pri></k_ele>
<r_ele><reb>なる</reb><re_pri>ichi1</re_pri></r_ele>
<sense><pos>&v5r;</pos><pos>&vi;</pos><misc>&uk;</misc><gloss>to become</gloss></sense>
<sense><gloss>to be promoted</gloss></sense>
</entry>
<entry><ent_seq>2</ent_seq>
<k_ele><keb>鳴る</keb></k_ele>
<r_ele><reb>なる</reb></r_ele>
<sense><pos>&v5r;</pos><gloss>to sound &amp; ring</gloss></sense>
</entry>
<entry><ent_seq>3</ent_seq>
<k_ele><keb>行く</keb><ke_pri>ichi1</ke_pri></k_ele>
<r_ele><reb>いく</reb></r_ele>
<sense><pos>&v5k-s;</pos><gloss>to go</gloss></sense>
</entry>
<entry><ent_seq>4</ent_seq>
<k_ele><keb>二</keb><ke_pri>ichi1</ke_pri></k_ele>
<r_ele><reb>に</reb></r_ele>
<sense><pos>&num;</pos><gloss>two</gloss></sense>
</entry>
</JMdict>
"""


@pytest.fixture(scope="module")
def jmdict(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("jmdict")
    source = tmp / "JMdict_e.gz"
    source.write_bytes(gzip.compress(JMDICT_SAMPLE.encode("utf-8")))
    path = tmp / "jmdict.sqlite"
    assert dictionary.build(path, source=source) == 4
    return dictionary.Dictionary(path)


def lookup(d, text):
    [word] = [w for w in analyze(text) if w.pos != "symbol"]
    return d.lookup(word.to_dict())


def test_kana_word_resolves_to_the_kanji_unidic_names(jmdict):
    [best, other] = lookup(jmdict, "なる")
    assert best["kanji"] == ["成る"]
    assert best["senses"][0]["pos"] == ["godan verb", "intransitive"]
    assert best["senses"][1]["pos"] == ["godan verb", "intransitive"]  # inherited
    assert best["senses"][0]["notes"] == ["word usually written using kana alone"]
    assert other["senses"][0]["gloss"] == ["to sound & ring"]


def test_conjugated_word_looks_up_its_dictionary_form(jmdict):
    assert lookup(jmdict, "行ったら")[0]["senses"][0]["gloss"] == ["to go"]


def test_missing_dictionary_raises(tmp_path):
    with pytest.raises(dictionary.DictionaryMissing):
        dictionary.Dictionary(tmp_path / "nope.sqlite")
