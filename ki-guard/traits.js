// Rule-based traits of a command. Not learned and not evaluated on labels (no labelled data exists for them).
'use strict';

const ROOT = /\b(sudo|su\s+-|doas|runas|pkexec)\b|start-process\b[^\n]*-verb\s+runas|\/etc\/(passwd|shadow|sudoers|hosts|cron)|\b(chown|chmod)\b[^\n]*\s\/(etc|usr|bin|boot|var)|\bsystemctl\b|\bvisudo\b|\buseradd|usermod|passwd\b|hklm:|reg\s+(add|delete)\s+hklm|c:\\windows/i;

// Not reversible: deletes, overwrites, wipes, sends data out, rewrites history.
const IRREVERSIBLE = /\b(rm|shred|unlink|wipefs|mkfs\S*|dd|truncate)\b|\bremove-item\b|\b(del|erase|rd|rmdir)\b|>\s*\/dev\/(sd|nvme)|drop\s+(table|database)|delete\s+from|git\s+(push\s+[^\n]*(-f|--force)|reset\s+--hard|clean\s+-\w*f)|\b(curl|wget|nc|scp|rsync)\b[^\n]*(-d|--data|-F|-T|--upload|@|\s\S+:)|>\s*[^\s&|>]+|vssadmin\s+delete|kill\s+-9/i;

function traits(text) {
  const t = String(text);
  return { needs_root: ROOT.test(t), reversible: !IRREVERSIBLE.test(t) };
}

module.exports = { traits, ROOT };
