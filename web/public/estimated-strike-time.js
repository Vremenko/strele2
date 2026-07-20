/**
 * Ocenjeni čas udara: Europe/Ljubljana, nato zaokrožitev na 5 min (samo prikaz).
 * Enaka logika kot strele_archive/estimated_strike_time.py
 */
(function (global) {
  "use strict";

  var LJ = "Europe/Ljubljana";

  function parseInstant(input) {
    if (input == null || input === "") return null;
    if (input instanceof Date) {
      return isNaN(input.getTime()) ? null : input;
    }
    var s = String(input);
    var d = new Date(s.indexOf("T") >= 0 ? s : s + "T12:00:00");
    return isNaN(d.getTime()) ? null : d;
  }

  function ljParts(instant) {
    var parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: LJ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(instant);
    function n(type) {
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].type === type) return Number(parts[i].value);
      }
      return NaN;
    }
    return {
      year: n("year"),
      month: n("month"),
      day: n("day"),
      hour: n("hour"),
      minute: n("minute"),
      second: n("second"),
    };
  }

  function ljWallToUtc(year, month, day, hour, minute) {
    var utc = new Date(Date.UTC(year, month - 1, day, hour, minute, 0));
    for (var i = 0; i < 4; i++) {
      var p = ljParts(utc);
      var wanted = Date.UTC(year, month - 1, day, hour, minute);
      var actual = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
      var diff = wanted - actual;
      if (diff === 0) break;
      utc = new Date(utc.getTime() + diff);
    }
    return utc;
  }

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  /** UTC Date zaokroženega lokalnega časa, ali null. */
  function roundEstimatedStrikeInstant(input) {
    var d = parseInstant(input);
    if (!d) return null;
    var p = ljParts(d);
    var totalMin = p.hour * 60 + p.minute + p.second / 60;
    var rounded = Math.round(totalMin / 5) * 5;
    var year = p.year;
    var month = p.month;
    var day = p.day;
    if (rounded >= 24 * 60) {
      rounded -= 24 * 60;
      var next = new Date(Date.UTC(year, month - 1, day + 1));
      year = next.getUTCFullYear();
      month = next.getUTCMonth() + 1;
      day = next.getUTCDate();
    } else if (rounded < 0) {
      rounded += 24 * 60;
      var prev = new Date(Date.UTC(year, month - 1, day - 1));
      year = prev.getUTCFullYear();
      month = prev.getUTCMonth() + 1;
      day = prev.getUTCDate();
    }
    return ljWallToUtc(year, month, day, Math.floor(rounded / 60), rounded % 60);
  }

  function formatEstimatedStrikeTime(input) {
    var rounded = roundEstimatedStrikeInstant(input);
    if (!rounded) return "—";
    var p = ljParts(rounded);
    return pad2(p.hour) + "." + pad2(p.minute);
  }

  function formatEstimatedStrikeDateTime(input) {
    var rounded = roundEstimatedStrikeInstant(input);
    if (!rounded) return input ? String(input) : "—";
    var p = ljParts(rounded);
    return p.day + ". " + p.month + ". " + p.year + ", " + pad2(p.hour) + "." + pad2(p.minute);
  }

  global.StrelkoEstimatedStrikeTime = {
    roundEstimatedStrikeInstant: roundEstimatedStrikeInstant,
    formatEstimatedStrikeTime: formatEstimatedStrikeTime,
    formatEstimatedStrikeDateTime: formatEstimatedStrikeDateTime,
  };
})(typeof window !== "undefined" ? window : globalThis);
