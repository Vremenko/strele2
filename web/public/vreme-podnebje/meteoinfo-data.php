<?php
/**
 * Meteoinfo podatki v isti obliki kot lokalni /api/* (seštevek po občinah).
 */
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: no-store, max-age=0');

@set_time_limit(300);

$base = getenv('METEOINFO_API_BASE') ?: 'https://test.meteoinfo.si';
$mapFile = __DIR__ . '/data/embed-regije.json';
$action = $_GET['action'] ?? '';

function respond_error(int $code, string $message): void
{
    http_response_code($code);
    echo json_encode(['error' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

function meteoinfo_request(string $base, string $path, array $params): array
{
    $url = rtrim($base, '/') . $path . '?' . http_build_query($params);
    $ctx = stream_context_create([
        'http' => [
            'method' => 'GET',
            'timeout' => 60,
            'ignore_errors' => true,
            'header' => "Accept: application/json\r\nUser-Agent: vreme-podnebje-strele-data/1.0\r\n",
        ],
        'ssl' => ['verify_peer' => true, 'verify_peer_name' => true],
    ]);
    $body = @file_get_contents($url, false, $ctx);
    if ($body === false) {
        respond_error(502, 'Meteoinfo ni dosegljiv');
    }
    $statusLine = $http_response_header[0] ?? '';
    if (!preg_match('/\s200\s/', $statusLine)) {
        respond_error(502, 'Meteoinfo HTTP napaka');
    }
    $data = json_decode($body, true);
    if (!is_array($data)) {
        respond_error(502, 'Neveljaven JSON iz Meteoinfo');
    }
    return $data;
}

function day_window_utc(string $dayIso): array
{
    $tz = new DateTimeZone('Europe/Ljubljana');
    $utc = new DateTimeZone('UTC');
    $start = new DateTime($dayIso . ' 00:00:00', $tz);
    $end = (clone $start)->modify('+1 day')->modify('-1 second');
    return [
        $start->setTimezone($utc)->format('Y-m-d\TH:i:s\Z'),
        $end->setTimezone($utc)->format('Y-m-d\TH:i:s\Z'),
    ];
}

function hour_window_utc(string $dayIso, int $hour): array
{
    $tz = new DateTimeZone('Europe/Ljubljana');
    $utc = new DateTimeZone('UTC');
    $start = new DateTime(sprintf('%s %02d:00:00', $dayIso, $hour), $tz);
    $end = (clone $start)->modify('+1 hour')->modify('-1 second');
    return [
        $start->setTimezone($utc)->format('Y-m-d\TH:i:s\Z'),
        $end->setTimezone($utc)->format('Y-m-d\TH:i:s\Z'),
    ];
}

function municipalities_cells(string $base, string $from, string $to): array
{
    $data = meteoinfo_request($base, '/api/v1/strele/heatmap/municipalities', [
        'time_from_utc' => $from,
        'time_to_utc' => $to,
        'normalize' => 'false',
        'sort' => 'count',
        'limit' => 500,
    ]);
    return $data['cells'] ?? [];
}

function sum_cells(array $cells): int
{
    $sum = 0;
    foreach ($cells as $cell) {
        $sum += (int) ($cell['count'] ?? 0);
    }
    return $sum;
}

function day_total(string $base, string $dayIso): int
{
    [$from, $to] = day_window_utc($dayIso);
    return sum_cells(municipalities_cells($base, $from, $to));
}

function load_regije_map(string $mapFile): array
{
    if (!is_file($mapFile)) {
        respond_error(500, 'Manjka data/embed-regije.json');
    }
    $data = json_decode((string) file_get_contents($mapFile), true);
    if (!is_array($data) || !isset($data['regije'], $data['obcina_regija'])) {
        respond_error(500, 'Neveljaven embed-regije.json');
    }
    return $data;
}

function regije_from_obcine_totals(array $mapData, array $obcinaTotals): array
{
    $totals = [];
    foreach ($mapData['regije'] as $regija) {
        $totals[(int) $regija['id']] = 0;
    }
    foreach ($obcinaTotals as $obcinaId => $stevilo) {
        $regijaId = $mapData['obcina_regija'][(string) $obcinaId] ?? null;
        if ($regijaId === null) {
            continue;
        }
        $totals[(int) $regijaId] = ($totals[(int) $regijaId] ?? 0) + (int) $stevilo;
    }
    $rows = [];
    foreach ($mapData['regije'] as $regija) {
        $rid = (int) $regija['id'];
        $rows[] = ['regija' => $regija['ime'], 'stevilo' => $totals[$rid] ?? 0, '_id' => $rid];
    }
    usort($rows, static function (array $a, array $b): int {
        if ($a['stevilo'] !== $b['stevilo']) {
            return $b['stevilo'] <=> $a['stevilo'];
        }
        return strcmp($a['regija'], $b['regija']);
    });
    return array_map(static fn(array $row): array => [
        'regija' => $row['regija'],
        'stevilo' => $row['stevilo'],
    ], $rows);
}

function obcina_totals_for_day(string $base, string $dayIso): array
{
    [$from, $to] = day_window_utc($dayIso);
    $cells = municipalities_cells($base, $from, $to);
    $totals = [];
    foreach ($cells as $cell) {
        $id = (int) ($cell['code'] ?? 0);
        if ($id <= 0) {
            continue;
        }
        $totals[$id] = ($totals[$id] ?? 0) + (int) ($cell['count'] ?? 0);
    }
    return $totals;
}

function date_range(int $days): array
{
    $end = new DateTime('today', new DateTimeZone('Europe/Ljubljana'));
    $start = (clone $end)->modify('-' . ($days - 1) . ' days');
    $out = [];
    $cursor = clone $start;
    while ($cursor <= $end) {
        $out[] = $cursor->format('Y-m-d');
        $cursor->modify('+1 day');
    }
    return $out;
}

switch ($action) {
    case 'latest-date':
        $cursor = new DateTime('today', new DateTimeZone('Europe/Ljubljana'));
        for ($i = 0; $i < 120; $i++) {
            $day = $cursor->format('Y-m-d');
            if (day_total($base, $day) > 0) {
                echo json_encode(['datum' => $day], JSON_UNESCAPED_UNICODE);
                exit;
            }
            $cursor->modify('-1 day');
        }
        echo json_encode(['datum' => null], JSON_UNESCAPED_UNICODE);
        break;

    case 'si-daily':
        $days = max(1, min(365, (int) ($_GET['days'] ?? 30)));
        $result = [];
        foreach (date_range($days) as $day) {
            $result[] = ['datum' => $day, 'stevilo' => day_total($base, $day)];
            usleep(350000);
        }
        echo json_encode($result, JSON_UNESCAPED_UNICODE);
        break;

    case 'si-hourly':
        $days = isset($_GET['days']) ? (int) $_GET['days'] : null;
        $day = $_GET['day'] ?? null;
        if ($days !== null) {
            $days = max(1, min(365, $days));
            $hourly = array_fill(0, 24, 0);
            foreach (date_range($days) as $d) {
                if (day_total($base, $d) <= 0) {
                    continue;
                }
                for ($h = 0; $h < 24; $h++) {
                    [$from, $to] = hour_window_utc($d, $h);
                    $hourly[$h] += sum_cells(municipalities_cells($base, $from, $to));
                    usleep(120000);
                }
            }
            $result = [];
            for ($h = 0; $h < 24; $h++) {
                $result[] = ['ura' => $h, 'stevilo' => $hourly[$h]];
            }
            echo json_encode($result, JSON_UNESCAPED_UNICODE);
            break;
        }
        if (!$day || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $day)) {
            respond_error(422, 'Podaj day ali days');
        }
        $result = [];
        for ($h = 0; $h < 24; $h++) {
            [$from, $to] = hour_window_utc($day, $h);
            $result[] = ['ura' => $h, 'stevilo' => sum_cells(municipalities_cells($base, $from, $to))];
            if ($h > 0) {
                usleep(350000);
            }
        }
        echo json_encode($result, JSON_UNESCAPED_UNICODE);
        break;

    case 'regije-daily':
        $mapData = load_regije_map($mapFile);
        $days = isset($_GET['days']) ? (int) $_GET['days'] : null;
        $day = $_GET['day'] ?? null;
        $obcinaTotals = [];
        if ($days !== null) {
            $days = max(1, min(365, $days));
            foreach (date_range($days) as $d) {
                foreach (obcina_totals_for_day($base, $d) as $oid => $n) {
                    $obcinaTotals[$oid] = ($obcinaTotals[$oid] ?? 0) + $n;
                }
                usleep(250000);
            }
        } else {
            if (!$day || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $day)) {
                respond_error(422, 'Podaj day ali days');
            }
            $obcinaTotals = obcina_totals_for_day($base, $day);
        }
        echo json_encode(regije_from_obcine_totals($mapData, $obcinaTotals), JSON_UNESCAPED_UNICODE);
        break;

    default:
        respond_error(404, 'Neznana action');
}
