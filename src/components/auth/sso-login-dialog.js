class SsoLoginDialog extends HTMLElement {
  constructor() {
    super();
    this._visible = false;
    this._profiles = [];
  }

  connectedCallback() {
    this.style.display = 'none';
  }

  async show() {
    this._visible = true;
    this.style.display = 'block';

    // Load profiles
    if (window.electronAPI?.listProfiles) {
      this._profiles = await window.electronAPI.listProfiles();
    }

    this.render();
  }

  hide() {
    this._visible = false;
    this.style.display = 'none';
  }

  render() {
    this.innerHTML = `
      <div class="overlay">
        <div class="dialog">
          <h2>AWS SSO Login</h2>
          <div class="form-group">
            <label>SSO Profile</label>
            <select class="input sso-profile-select" style="width:100%;">
              ${this._profiles.map(p => `<option value="${p}">${p}</option>`).join('')}
              <option value="">Enter manually...</option>
            </select>
          </div>
          <div class="form-group manual-input" style="display:none;">
            <label>Profile Name</label>
            <input class="input sso-manual-profile" style="width:100%;" placeholder="e.g. my-sso-profile">
          </div>
          <div class="form-group" style="display:flex;gap:8px;margin-top:16px;">
            <button class="btn btn-primary sso-login-btn" style="flex:1;">Login</button>
          </div>
          <div class="sso-status" style="margin-top:8px;font-size:12px;color:var(--color-text-muted,#6a6a6a);"></div>
        </div>
      </div>
    `;

    const select = this.querySelector('.sso-profile-select');
    const manualInput = this.querySelector('.manual-input');

    select?.addEventListener('change', () => {
      manualInput.style.display = select.value === '' ? 'block' : 'none';
    });

    this.querySelector('.sso-login-btn')?.addEventListener('click', () => this._login());
  }

  async _login() {
    const select = this.querySelector('.sso-profile-select');
    const manual = this.querySelector('.sso-manual-profile');
    const status = this.querySelector('.sso-status');
    const profile = select?.value || manual?.value?.trim();

    if (!profile) {
      status.textContent = 'Please select or enter a profile.';
      status.style.color = 'var(--color-error, #f44747)';
      return;
    }

    status.textContent = 'Logging in...';
    status.style.color = 'var(--color-text-secondary, #9d9d9d)';

    try {
      if (window.electronAPI?.ssoLogin) {
        const result = await window.electronAPI.ssoLogin(profile);
        if (result.success) {
          // Save settings
          await window.electronAPI.saveSettings({ awsProfile: profile });
          status.textContent = '✓ Login successful!';
          status.style.color = 'var(--color-success, #4ec9b0)';
          setTimeout(() => this.hide(), 1000);
        } else {
          status.textContent = `Login failed: ${result.error}`;
          status.style.color = 'var(--color-error, #f44747)';
        }
      }
    } catch (e) {
      status.textContent = `Error: ${e.message}`;
      status.style.color = 'var(--color-error, #f44747)';
    }
  }
}

customElements.define('sso-login-dialog', SsoLoginDialog);
