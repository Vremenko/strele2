<?php
/**
 * Posrednik do Meteoinfo API (obide CORS v brskalniku).
 * Dovoljeni samo aggregates/series klici.
 */
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, max-age=0');

$allowed = [
    '/api/v1/strele/aggregates/series',
];

$path = $_GET['path'] ?? '/api/v1/strele/aggregates/series';
if (!in_array($path, $allowed, true)) {
    http_response_code(403);
    echo json_encode(['error' => 'Pot ni dovoljena']);
    exit;
}

$params = $_GET;
unset($params['path']);

$base = getenv('METEOINFO_API_BASE') ?: 'https://test.meteoinfo.si';
$url = rtrim($base, '/') . $path;
if ($params !== []) {
    $url .= '?' . http_build_query($params);
}

$ctx = stream_context_create([
    'http' => [
        'method' => 'GET',
        'timeout' => 90,
        'ignore_errors' => true,
        'header' => "Accept: application/json\r\nUser-Agent: vreme-podnebje-strele-proxy/1.0\r\n",
    ],
    'ssl' => [
        'verify_peer' => true,
        'verify_peer_name' => true,
    ],
]);

$body = @file_get_contents($url, false, $ctx);
if ($body === false) {
    http_response_code(502);
    echo json_encode(['error' => 'Meteoinfo ni dosegljiv']);
    exit;
}

$statusLine = $http_response_header[0] ?? 'HTTP/1.1 502 Bad Gateway';
preg_match('/\d{3}/', $statusLine, $m);
$status = (int) ($m[0] ?? 502);
http_response_code($status);
echo $body;
