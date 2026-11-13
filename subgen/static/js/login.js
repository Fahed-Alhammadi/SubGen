// Sign-in: username and password first; the authenticator code step appears only when the
// server reports that two-factor sign-in is enabled.
const form = document.getElementById('login-form');
const errorEl = document.getElementById('login-error');
const title = document.getElementById('login-title');
const submit = form.querySelector('[type=submit]');
const back = form.querySelector('[data-back]');
const steps = {
  password: form.querySelector('[data-step=password]'),
  totp: form.querySelector('[data-step=totp]'),
};
let step = 'password';

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function showStep(name) {
  step = name;
  steps.password.hidden = name !== 'password';
  steps.totp.hidden = name !== 'totp';
  back.hidden = name !== 'totp';
  title.textContent = name === 'totp' ? 'Two-factor sign-in' : 'Sign in';
  submit.textContent = name === 'totp' ? 'Verify' : 'Sign in';
  errorEl.hidden = true;
  if (name === 'totp') {
    form.totp_code.value = '';
    form.totp_code.focus();
  } else {
    form.password.value = '';
    form.password.focus();
  }
}

/** Return to the page the user asked for before signing in (same-site paths only). */
function destination() {
  const next = new URLSearchParams(window.location.search).get('next') || '/';
  return next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\') ? next : '/';
}

async function post(path, fields) {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => body.append(key, value));
  const response = await fetch(path, { method: 'POST', body, credentials: 'same-origin', headers: { accept: 'application/json' } });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { response, data, detail: data && typeof data.detail === 'string' ? data.detail : '' };
}

function validate() {
  if (step === 'password') {
    const username = form.username.value.trim();
    if (!username || !form.password.value) {
      showError('Enter your username and password.');
      (username ? form.password : form.username).focus();
      return false;
    }
    return true;
  }
  if (!/^\d{6}$/.test(form.totp_code.value.trim())) {
    showError('The authenticator code is six digits.');
    form.totp_code.focus();
    return false;
  }
  return true;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorEl.hidden = true;
  if (!validate()) return;
  const label = submit.textContent;
  submit.disabled = true;
  submit.textContent = step === 'totp' ? 'Verifying…' : 'Signing in…';
  try {
    const { response, data, detail } = step === 'password'
      ? await post('/api/login', { username: form.username.value.trim(), password: form.password.value })
      : await post('/api/login/totp', { totp_code: form.totp_code.value.trim() });
    if (response.ok && data && data.totp_required) {
      showStep('totp');
    } else if (response.ok) {
      window.location.assign(destination());
      return;
    } else if (response.status === 429) {
      showError(detail || 'Too many sign-in attempts. Wait a minute and try again.');
    } else if (step === 'totp' && /expired/i.test(detail)) {
      showStep('password');
      showError(detail);
    } else {
      showError(detail || 'Sign-in failed. Check your details and try again.');
      (step === 'totp' ? form.totp_code : form.password).select();
    }
  } catch {
    showError('Cannot reach the SubGen server. Check that it is running.');
  }
  submit.disabled = false;
  if (submit.textContent.endsWith('…')) submit.textContent = label;
});

back.addEventListener('click', () => showStep('password'));
