<?php
/**
 * FURS HTTPS posrednik — klic iz StormAPI (tujina) → FURS (SLO egress prek Neoserv).
 *
 * StormAPI podpiše JWT lokalno (.p12 na Hetznerju); ta skripta samo pošlje zahtevo
 * na FURS z mTLS certifikatom iz .p12 na Neoserv (slovenski IP).
 */
declare(strict_types=1);

/**
 * Prebere certifikat (PEM ali DER) in vrne PEM.
 */
function furs_read_cert_pem(string $path): ?string
{
    if (!is_readable($path)) {
        return null;
    }
    $data = file_get_contents($path);
    if ($data === false || $data === '') {
        return null;
    }
    if (str_contains($data, 'BEGIN CERTIFICATE')) {
        return trim($data) . "\n";
    }
    $b64 = chunk_split(base64_encode($data), 64, "\n");
    return "-----BEGIN CERTIFICATE-----\n" . $b64 . "-----END CERTIFICATE-----\n";
}

/**
 * Združi SIGOV-CA2 + SI-TRUST Root v temp PEM (FURS v3.2 CA pinning).
 *
 * @param array<string, mixed> $config
 */
function furs_build_tls_ca_bundle(bool $production, array $config): ?string
{
    $certsDir = rtrim((string) ($config['certs_dir'] ?? __DIR__ . '/certs'), '/');
    $chain = [
        $certsDir . '/sigov-ca2.xcert.crt',
        $certsDir . '/si-trust-root.crt',
    ];

    $pem = '';
    foreach ($chain as $path) {
        $chunk = furs_read_cert_pem($path);
        if ($chunk === null) {
            return null;
        }
        $pem .= $chunk;
    }

    $bundleFile = tempnam(sys_get_temp_dir(), 'furs-ca-');
    if ($bundleFile === false) {
        return null;
    }
    file_put_contents($bundleFile, $pem);
    @chmod($bundleFile, 0600);

    return $bundleFile;
}

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Samo POST']);
    exit;
}

$configPath = __DIR__ . '/config.php';
if (!is_readable($configPath)) {
    http_response_code(503);
    echo json_encode(['error' => 'Manjka config.php — glej config.example.php']);
    exit;
}

/** @var array<string, mixed> $config */
$config = require $configPath;

$serviceKey = (string) ($config['service_key'] ?? '');
$providedKey = (string) ($_SERVER['HTTP_X_SERVICE_KEY'] ?? '');
if ($serviceKey === '' || !hash_equals($serviceKey, $providedKey)) {
    http_response_code(401);
    echo json_encode(['error' => 'Neveljaven X-Service-Key']);
    exit;
}

$allowedIps = $config['allowed_ips'] ?? [];
if (is_array($allowedIps) && $allowedIps !== []) {
    $clientIp = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
    if (!in_array($clientIp, $allowedIps, true)) {
        http_response_code(403);
        echo json_encode(['error' => 'IP ni dovoljen', 'ip' => $clientIp]);
        exit;
    }
}

$raw = file_get_contents('php://input');
if ($raw === false || $raw === '') {
    http_response_code(400);
    echo json_encode(['error' => 'Prazno telo']);
    exit;
}

/** @var mixed $payload */
$payload = json_decode($raw, true);
if (!is_array($payload)) {
    http_response_code(400);
    echo json_encode(['error' => 'Neveljaven JSON']);
    exit;
}

$production = !empty($payload['production']);
$path = (string) ($payload['path'] ?? '');
$jsonBody = $payload['json'] ?? null;

if ($path === '' || !is_array($jsonBody)) {
    http_response_code(400);
    echo json_encode(['error' => 'Manjka path ali json']);
    exit;
}

$allowedPaths = [
    'v1/cash_registers/echo',
    'v1/cash_registers/invoices',
    'v1/cash_registers/invoices/register',
];
if (!in_array($path, $allowedPaths, true)) {
    http_response_code(403);
    echo json_encode(['error' => 'Pot ni dovoljena', 'path' => $path]);
    exit;
}

$p12Path = (string) ($config['p12_path'] ?? '');
$p12Password = (string) ($config['p12_password'] ?? '');
if ($p12Path === '' || !is_readable($p12Path)) {
    http_response_code(503);
    echo json_encode(['error' => 'P12 certifikat ni dostopen']);
    exit;
}

$p12Data = file_get_contents($p12Path);
if ($p12Data === false) {
    http_response_code(503);
    echo json_encode(['error' => 'P12 ni mogoče prebrati']);
    exit;
}

$certs = [];
if (!openssl_pkcs12_read($p12Data, $certs, $p12Password)) {
    http_response_code(503);
    echo json_encode(['error' => 'P12 geslo ali datoteka ni veljavna']);
    exit;
}

$certPem = (string) ($certs['cert'] ?? '');
$keyPem = (string) ($certs['pkey'] ?? '');
if ($certPem === '' || $keyPem === '') {
    http_response_code(503);
    echo json_encode(['error' => 'P12 ne vsebuje certifikata ali ključa']);
    exit;
}

$tmpDir = sys_get_temp_dir();
$certFile = tempnam($tmpDir, 'furs-cert-');
$keyFile = tempnam($tmpDir, 'furs-key-');
if ($certFile === false || $keyFile === false) {
    http_response_code(500);
    echo json_encode(['error' => 'Temp datoteke']);
    exit;
}

file_put_contents($certFile, $certPem);
file_put_contents($keyFile, $keyPem);
@chmod($certFile, 0600);
@chmod($keyFile, 0600);

$base = $production
    ? 'https://blagajne.fu.gov.si:9003'
    : 'https://blagajne-test.fu.gov.si:9002';
$url = rtrim($base, '/') . '/' . ltrim($path, '/');
$body = json_encode($jsonBody, JSON_UNESCAPED_UNICODE);
if ($body === false) {
    @unlink($certFile);
    @unlink($keyFile);
    http_response_code(400);
    echo json_encode(['error' => 'JSON telo ni veljavno']);
    exit;
}

$timeout = (int) ($config['timeout'] ?? 30);
if ($timeout < 5) {
    $timeout = 30;
}

$tlsVerify = ($config['tls_verify'] ?? true) !== false;
$caBundleFile = null;
$curlOpts = [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => $body,
    CURLOPT_HTTPHEADER => [
        'Content-Type: application/json; charset=UTF-8',
        'Accept: application/json',
        'User-Agent: vreme-podnebje-furs-relay/1.1',
    ],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HEADER => true,
    CURLOPT_TIMEOUT => $timeout,
    CURLOPT_SSLCERT => $certFile,
    CURLOPT_SSLKEY => $keyFile,
    CURLOPT_SSLVERSION => CURL_SSLVERSION_TLSv1_2,
];

if ($tlsVerify) {
    $caBundleFile = furs_build_tls_ca_bundle($production, $config);
    if ($caBundleFile === null) {
        @unlink($certFile);
        @unlink($keyFile);
        http_response_code(503);
        echo json_encode(['error' => 'Manjka FURS CA veriga (certs/) — glej DEPLOY.txt']);
        exit;
    }
    $curlOpts[CURLOPT_SSL_VERIFYPEER] = true;
    $curlOpts[CURLOPT_SSL_VERIFYHOST] = 2;
    $curlOpts[CURLOPT_CAINFO] = $caBundleFile;
} else {
    $curlOpts[CURLOPT_SSL_VERIFYPEER] = false;
    $curlOpts[CURLOPT_SSL_VERIFYHOST] = 0;
}

$ch = curl_init($url);
curl_setopt_array($ch, $curlOpts);

$response = curl_exec($ch);
$curlErr = curl_error($ch);
$status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
$headerSize = (int) curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);

@unlink($certFile);
@unlink($keyFile);
if ($caBundleFile !== null) {
    @unlink($caBundleFile);
}

if ($response === false) {
    http_response_code(502);
    echo json_encode(['error' => 'FURS ni dosegljiv', 'detail' => $curlErr]);
    exit;
}

$responseHeaders = substr($response, 0, $headerSize);
$responseBody = substr($response, $headerSize);
$contentType = 'application/json; charset=utf-8';
foreach (explode("\r\n", $responseHeaders) as $line) {
    if (stripos($line, 'Content-Type:') === 0) {
        $contentType = trim(substr($line, strlen('Content-Type:')));
        break;
    }
}

http_response_code($status > 0 ? $status : 502);
header('Content-Type: ' . $contentType);
echo $responseBody;
