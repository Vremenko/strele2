/**
 * Slovensko oblikovanje celih števil (pika kot ločilo tisočic).
 * Enaka logika kot strele_archive/sl_number_format.py
 */
(function (global) {
  "use strict";

  function formatSlInt(value) {
    if (value == null || value === false) return "—";
    if (typeof value === "boolean") return "—";
    if (typeof value === "string" && !String(value).trim()) return "—";
    var n = Number(value);
    if (!Number.isFinite(n)) return "—";
    n = Math.round(n);
    var sign = n < 0 ? "-" : "";
    var digits = String(Math.abs(n));
    var parts = [];
    while (digits.length > 3) {
      parts.unshift(digits.slice(-3));
      digits = digits.slice(0, -3);
    }
    parts.unshift(digits);
    return sign + parts.join(".");
  }

  global.StrelkoSlNumberFormat = {
    formatSlInt: formatSlInt,
  };
})(typeof window !== "undefined" ? window : globalThis);
