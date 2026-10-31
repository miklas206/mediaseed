"""Preserve reported ISO language codes; aliases only normalise known equivalents."""
import re
ROWS=[('en','English','eng'),('ar','Arabic','ara'),('bg','Bulgarian','bul'),('zh','Chinese','zho chi'),('cs','Czech','ces cze'),('da','Danish','dan dk'),('nl','Dutch','nld dut'),('et','Estonian','est'),('fi','Finnish','fin'),('fr','French','fra fre'),('de','German','deu ger'),('el','Greek','ell gre'),('he','Hebrew','heb'),('hu','Hungarian','hun'),('id','Indonesian','ind'),('it','Italian','ita'),('ja','Japanese','jpn'),('ko','Korean','kor'),('lv','Latvian','lav'),('lt','Lithuanian','lit'),('no','Norwegian','nor nob nno nb nn'),('pl','Polish','pol'),('pt','Portuguese','por'),('ro','Romanian','ron rum'),('ru','Russian','rus'),('sk','Slovak','slk slo'),('sl','Slovenian','slv'),('es','Spanish','spa'),('sv','Swedish','swe se'),('th','Thai','tha'),('tr','Turkish','tur'),('uk','Ukrainian','ukr')]
NAMES={code:name for code,name,_ in ROWS}
ALIASES={alias:code for code,name,aliases in ROWS for alias in [code,name.lower(),*aliases.split()]}
def language_code(value):
    value=str(value or '').strip().lower().replace('_','-')
    if value in ALIASES:return ALIASES[value]
    if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]+)*',value):return None
    code=value.split('-')[0]
    return None if code in {'und','mul','zxx'} else ALIASES.get(code,code)
