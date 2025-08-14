function patchVerticalLyrics(lines) {
  return lines.map(l => {
    if (!l.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u)) return l
    return l
      .replaceAll("“", "「")
      .replaceAll("”", "」")
      .replaceAll("‘", "『")
      .replaceAll("‘", "』")
      .replaceAll("…", "⋯")
      .replace(/(?<![^\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana} 　！-～])\d{1,2}(?![^\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana} 　！-～])/ug, "<hori>$&</hori>")
  })
}

function rubyify(str) {
  return str.replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\[([^\n\]]+)\]\{([^\n\}]+(?:\}\{[^\n\}]+)*)\}/g, (_, base, ruby) => ruby.split("}{").reduce((base, ruby) => `<ruby>${base}<rt>${ruby}</rt></ruby>`, base))
    .replace(/&lt;hori&gt;(.+?)&lt;\/hori&gt;/g, '<span class="hori">$1</span>')
}
