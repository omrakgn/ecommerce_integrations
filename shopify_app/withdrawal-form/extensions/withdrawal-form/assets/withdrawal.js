// Withdrawal form ("Vertrag widerrufen", § 356a BGB / Art. 11a Directive 2011/83/EU).
// Fills every [data-withdrawal] block on the page. The declaration goes to ERPNext
// (multichannel_core.returns.withdrawal.submit), which keeps it, sends the acknowledgement
// of receipt by email at once and opens the return case.
// Rules that must stay: no login, no CAPTCHA, the reason stays optional.
(function () {
  var count = 0;

  function fill(text, values) {
    return text.replace(/\{(\w+)\}/g, function (match, key) {
      return values[key] != null ? values[key] : '';
    });
  }

  function make(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text) node.textContent = text;
    return node;
  }

  function field(id, label, input, hint) {
    var wrap = make('div', { class: 'withdrawal-field' });
    wrap.appendChild(make('label', { for: id }, label));
    wrap.appendChild(input);
    if (hint) {
      input.setAttribute('aria-describedby', id + '-hint');
      wrap.appendChild(make('span', { class: 'withdrawal-hint', id: id + '-hint' }, hint));
    }
    return wrap;
  }

  function init(root) {
    if (root.getAttribute('data-ready')) return;
    root.setAttribute('data-ready', '1');
    count += 1;
    var p = 'wd' + count + '-';
    var lang = (root.getAttribute('data-lang') || 'en').slice(0, 2);
    // The texts come from the extension's locales in the shopper's language (blocks/withdrawal-form.liquid).
    var source = root.querySelector('script[data-withdrawal-texts]');
    var t;
    try { t = JSON.parse(source.textContent); } catch (e) { return; }
    var endpoint = root.getAttribute('data-endpoint');
    var fallback = root.getAttribute('data-fallback');

    root.innerHTML = '';
    root.appendChild(make('h1', { class: 'withdrawal-title' }, t.title));
    root.appendChild(make('p', { class: 'withdrawal-intro' }, t.intro));

    var formNode = make('form', { class: 'withdrawal-form', novalidate: '' });
    var nameInput = make('input', { type: 'text', id: p + 'name', name: 'full_name', autocomplete: 'name', required: '', maxlength: '140' });
    var orderInput = make('input', { type: 'text', id: p + 'order', name: 'order_reference', required: '', maxlength: '60' });
    var emailInput = make('input', { type: 'email', id: p + 'email', name: 'email', autocomplete: 'email', required: '', maxlength: '140' });
    formNode.appendChild(field(p + 'name', t.name, nameInput));
    formNode.appendChild(field(p + 'order', t.order, orderInput, t.order_hint));
    formNode.appendChild(field(p + 'email', t.email, emailInput));

    var scopeWrap = make('div', { class: 'withdrawal-field' });
    var fieldset = make('fieldset');
    fieldset.appendChild(make('legend', {}, t.scope));
    [['all', t.scope_all], ['some', t.scope_some]].forEach(function (pair, index) {
      var choice = make('label', { class: 'withdrawal-choice' });
      var radio = make('input', { type: 'radio', name: 'scope', value: pair[0] });
      if (index === 0) radio.checked = true;
      choice.appendChild(radio);
      choice.appendChild(document.createTextNode(pair[1]));
      fieldset.appendChild(choice);
    });
    scopeWrap.appendChild(fieldset);
    formNode.appendChild(scopeWrap);

    var itemsInput = make('textarea', { id: p + 'items', name: 'items', maxlength: '1000' });
    var itemsField = field(p + 'items', t.items, itemsInput);
    itemsField.hidden = true;
    formNode.appendChild(itemsField);
    formNode.appendChild(field(p + 'reason', t.reason, make('textarea', { id: p + 'reason', name: 'reason', maxlength: '1000' })));

    // Robots fill every field; people never see this one.
    var trap = make('div', { class: 'withdrawal-trap', 'aria-hidden': 'true' });
    trap.appendChild(make('input', { type: 'text', name: 'website', tabindex: '-1', autocomplete: 'off' }));
    formNode.appendChild(trap);

    var button = make('button', { type: 'submit', class: 'withdrawal-submit' }, t.confirm);
    formNode.appendChild(button);
    var status = make('div', { class: 'withdrawal-status', role: 'status', 'aria-live': 'polite' });
    root.appendChild(formNode);
    root.appendChild(status);

    formNode.addEventListener('change', function () {
      var some = formNode.querySelector('input[name="scope"]:checked').value === 'some';
      itemsField.hidden = !some;
      if (some) itemsInput.setAttribute('required', ''); else itemsInput.removeAttribute('required');
    });

    function showError() {
      status.className = 'withdrawal-status is-error';
      status.textContent = fill(t.error, { fallback: fallback });
      button.disabled = false;
      button.textContent = t.confirm;
    }

    formNode.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!formNode.checkValidity()) {
        formNode.reportValidity();
        return;
      }
      var data = new FormData(formNode);
      var body = new URLSearchParams();
      data.forEach(function (value, key) { body.append(key, value); });
      body.append('language', lang);
      try { body.append('time_zone', Intl.DateTimeFormat().resolvedOptions().timeZone || ''); } catch (e) {}
      body.append('page', window.location.href.split('?')[0]);
      button.disabled = true;
      button.textContent = t.sending;
      status.textContent = '';
      // No cookies: the declaration needs no login and must not depend on one.
      fetch(endpoint, { method: 'POST', body: body, credentials: 'omit' })
        .then(function (response) {
          return response.json().then(function (json) { return { ok: response.ok, json: json }; });
        })
        .then(function (result) {
          var answer = result.json && result.json.message;
          if (!result.ok || !answer || !answer.ok) return showError();
          var done = make('div', { class: 'withdrawal-done' });
          done.appendChild(make('h2', {}, t.done_title));
          done.appendChild(make('p', {}, fill(t.done, {
            date: answer.received_at || '',
            email: data.get('email'),
            ref: answer.reference || ''
          })));
          if (answer.found === false) {
            done.appendChild(make('p', { class: 'withdrawal-note' }, fill(t.not_found, {
              order: data.get('order_reference'),
              contact: answer.contact_email || fallback
            })));
          }
          formNode.remove();
          status.className = 'withdrawal-status';
          status.textContent = '';
          status.appendChild(done);
          status.scrollIntoView({ behavior: 'smooth', block: 'start' });
        })
        .catch(showError);
    });
  }

  function initAll() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-withdrawal]'), init);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
  else initAll();
  // The theme editor redraws a block when its settings change.
  document.addEventListener('shopify:section:load', initAll);
  document.addEventListener('shopify:block:select', initAll);
})();
