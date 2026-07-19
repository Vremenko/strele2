<?php
/**
 * Kopiraj v config.php in izpolni. config.php ne nalagaj v git / javni repozitorij.
 */
declare(strict_types=1);

return [
    // Skupna skrivnost — enaka kot FURS_RELAY_KEY na StormAPI (Hetzner).
    'service_key' => 'ZAMENJAJ-DOLGO-NAKLJUČNO-GESLO',

    // Najlažje: .p12 v ISTO mapo kot config.php (FTP: /strele/furs-relay/)
    'p12_path' => __DIR__ . '/61712949-3.p12',
    // Alternativa (absolutna pot na Neoserv, če .p12 leži drugje):
    // 'p12_path' => '/home/UPORABNIK/domains/vreme-podnebje.si/public_html/strele/furs-relay/61712949-3.p12',
    'p12_password' => 'GESLO_IZ_P12',

    // Opcijsko: dovoli samo IP StormAPI (Hetzner). Prazno = brez omejitve.
    'allowed_ips' => [
        // '167.235.61.137',
    ],

    // Timeout v sekundah (FURS + mTLS).
    'timeout' => 30,

    // TLS preverjanje FURS strežnika (SIGOV-CA2 + SI-TRUST Root, v3.2). false = samo za debug.
    'tls_verify' => true,

    // Mapa z javnimi CA datotekami (certs/ iz repozitorija).
    'certs_dir' => __DIR__ . '/certs',
];
