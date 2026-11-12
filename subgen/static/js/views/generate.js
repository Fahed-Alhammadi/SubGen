// Shared "generate subtitles" form used by Studio (inline) and the library
// media browser (dialog). Posts to POST /api/media/{id}/generate.
import { api } from '../api.js';
import { html, element } from '../ui/dom.js';
import { icon } from '../ui/icons.js';
import { createLangPicker, languageOptions } from '../ui/lang-picker.js';
import { openDialog } from '../ui/dialog.js';
import { notify } from '../ui/toast.js';
import { getLanguages } from '../store.js';
import { clock, plural } from '../ui/format.js';

const FORMATS = [
  ['srt', 'SRT'],
  ['ass', 'ASS'],
  ['vtt', 'WebVTT'],
];

let uid = 0;

function trackLabel(track, i) {
  const parts = [`Track ${i + 1}`];
  if (track.language) parts.push(track.language.toUpperCase());
  if (track.codec) parts.push(track.codec.toUpperCase());
  if (track.channels) parts.push(`${track.channels} ch`);
  if (track.title) parts.push(`“${track.title}”`);
  if (track.default) parts.push('(default)');
  return parts.join(', ');
}

/**
 * buildGenerateForm({media, probe, languages, defaults}) → {el, payload(), validate()}
 * `defaults` = {languages: [], formats: []}
 */
export function buildGenerateForm({ media, probe, languages, defaults = {} }) {
  uid += 1;
  const id = `gen-${uid}`;
  const formats = new Set(defaults.formats && defaults.formats.length ? defaults.formats : ['srt']);
  const audio = probe && Array.isArray(probe.audio) ? probe.audio : [];

  const el = element(html`
    <div class="generate-form stack">
      <div class="lang-slot"></div>
      <fieldset class="fieldset">
        <legend>Formats</legend>
        <div class="check-row">
          ${FORMATS.map(
            ([value, label]) => html`<label class="check"><input type="checkbox" name="formats" value="${value}" ${formats.has(value) ? 'checked' : ''}>${label}</label>`,
          )}
        </div>
      </fieldset>
      <div class="form-grid">
        <div class="field">
          <label for="${id}-src">Spoken language</label>
          <select class="select" id="${id}-src" name="source_language">
            ${languageOptions(languages.filter((l) => l.whisper !== false), 'auto', { includeAuto: true })}
          </select>
          <span class="field-help">Set this when detection picks the wrong language.</span>
        </div>
        <div class="field">
          <label for="${id}-audio">Audio track</label>
          <select class="select" id="${id}-audio" name="audio_track" ${audio.length ? '' : 'disabled'}>
            <option value="">${audio.length ? 'Use the default track' : 'Default track (no track info)'}</option>
            ${audio.map((t, i) => html`<option value="${t.index}">${trackLabel(t, i)}</option>`)}
          </select>
          ${probe === null ? html`<span class="field-help">Track details need ffprobe on the server.</span>` : ''}
        </div>
        <div class="field span-2">
          <label for="${id}-prompt">Initial prompt <span class="muted">(optional)</span></label>
          <textarea class="textarea" id="${id}-prompt" name="initial_prompt" rows="2" maxlength="800"
            placeholder="Names, places or jargon the speakers use, e.g. Daenerys, Westeros, dragonglass"></textarea>
          <span class="field-help">Helps Whisper spell names and terms correctly.</span>
        </div>
        <div class="field">
          <label for="${id}-prio">Priority</label>
          <input class="input" type="number" id="${id}-prio" name="priority" min="-100" max="100" step="1" value="0">
          <span class="field-help">Higher numbers run first (−100 to 100).</span>
        </div>
      </div>
    </div>`);

  const picker = createLangPicker({
    languages,
    selected: defaults.languages || [],
    label: 'Subtitle languages',
    help: 'One subtitle is created per language and format.',
  });
  el.querySelector('.lang-slot').append(picker.el);

  return {
    el,
    validate() {
      if (!picker.value.length) return 'Choose at least one subtitle language.';
      if (!el.querySelectorAll('input[name=formats]:checked').length) return 'Choose at least one format.';
      const prio = Number(el.querySelector('[name=priority]').value);
      if (!Number.isInteger(prio) || prio < -100 || prio > 100) return 'Priority must be a whole number from −100 to 100.';
      return '';
    },
    payload() {
      const trackValue = el.querySelector('[name=audio_track]').value;
      const options = { source_language: el.querySelector('[name=source_language]').value || 'auto' };
      if (trackValue !== '') options.audio_track = Number(trackValue);
      const prompt = el.querySelector('[name=initial_prompt]').value.trim();
      if (prompt) options.initial_prompt = prompt;
      return {
        languages: picker.value,
        formats: Array.from(el.querySelectorAll('input[name=formats]:checked')).map((b) => b.value),
        priority: Number(el.querySelector('[name=priority]').value) || 0,
        options,
      };
    },
    media,
  };
}

export async function submitGenerate(mediaId, payload) {
  const res = await api.post(`/api/media/${encodeURIComponent(mediaId)}/generate`, payload);
  const count = res && Array.isArray(res.jobs) ? res.jobs.length : 0;
  notify.ok(`Queued ${plural(count, 'subtitle job')}.`);
  return res;
}

/**
 * Open the generate dialog for a media item. Resolves with the generate
 * response, or undefined when cancelled.
 */
export async function openGenerateDialog(mediaId, defaults = {}) {
  let media;
  let languages;
  try {
    [media, languages] = await Promise.all([api.get(`/api/media/${encodeURIComponent(mediaId)}`), getLanguages()]);
  } catch (err) {
    notify.error(err);
    return undefined;
  }
  const form = buildGenerateForm({ media, probe: media.probe ?? null, languages, defaults });
  const header = element(html`<p class="generate-target muted small">
      ${icon('film')} <span class="name break"></span> ${media.duration_ms ? html`<span class="num">(${clock(media.duration_ms)})</span>` : ''}
    </p>`);
  header.querySelector('.name').textContent = media.name;
  const wrap = document.createElement('form');
  wrap.className = 'stack';
  wrap.noValidate = true;
  wrap.append(header, form.el);
  const formId = `genform-${mediaId}-${Date.now()}`;
  wrap.id = formId;

  const dlg = openDialog({
    title: 'Generate subtitles',
    size: 'wide',
    body: wrap,
    footer: html`<button type="button" class="btn" data-dialog-close>Cancel</button>
      <button type="submit" class="btn btn-primary" form="${formId}">${icon('sparkle')}Queue subtitles</button>`,
  });
  wrap.addEventListener('submit', async (event) => {
    event.preventDefault();
    const problem = form.validate();
    if (problem) {
      notify.error(problem);
      return;
    }
    const button = dlg.foot.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const res = await submitGenerate(mediaId, form.payload());
      dlg.close(res);
    } catch (err) {
      notify.error(err);
      button.disabled = false;
    }
  });
  return dlg.closed;
}
