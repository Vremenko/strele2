# Primeri poizvedb za grafe

-- 30 dni, celotna Slovenija
SELECT datum, stevilo
FROM strele_si_dnevno
WHERE datum >= CURRENT_DATE - 29
ORDER BY datum;

-- Urni profil za izbran dan
SELECT ura, stevilo
FROM strele_si_urno
WHERE datum = CURRENT_DATE
ORDER BY ura;

-- Regije za dan
SELECT r.ime_sl, s.stevilo
FROM strele_regija_dnevno s
JOIN regije r ON r.id = s.regija_id
WHERE s.datum = CURRENT_DATE
ORDER BY s.stevilo DESC;

-- Regija po urah (npr. Gorenjska = 9)
SELECT ura, stevilo
FROM strele_regija_urno
WHERE regija_id = 9 AND datum = CURRENT_DATE
ORDER BY ura;
