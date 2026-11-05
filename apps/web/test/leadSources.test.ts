// One lead list (docs/business.md#whatsapp-leads): names, stages and links of WhatsApp leads.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { conversationHref, salesToolsHref, stageText, waitingName, whatsappTarget } from '../src/lib/leadSources.ts';
import { setLocale } from '../src/lib/i18n.ts';

describe('WhatsApp leads in the one lead list', () => {
  test('the name is the company, then the person, then the phone, never invented', () => {
    assert.equal(waitingName({ company_name: 'Sara Bakery', contact_name: 'Sara', phone: '+971501234567' }), 'Sara Bakery');
    assert.equal(waitingName({ company_name: ' ', contact_name: 'Sara', phone: null }), 'Sara');
    assert.equal(waitingName({ company_name: '', contact_name: '', phone: '+971501234567' }), '+971501234567');
    assert.equal(waitingName({ company_name: '', contact_name: '', phone: null }), 'Unknown sender');
  });
  test('stages read in the person\'s language; an unknown stage is shown as it is', () => {
    assert.equal(stageText('quoted'), 'Quoted');
    setLocale('ar');
    try { assert.equal(stageText('quoted'), 'أُعد عرض سعر'); } finally { setLocale('en'); }
    assert.equal(stageText('custom'), 'custom');
  });
  test('links go to the conversation and to the sales tools, and unsafe ids fall back to WhatsApp', () => {
    assert.equal(conversationHref('conv_1'), '#whatsapp/conv_1');
    assert.equal(conversationHref('../x'), '#whatsapp');
    assert.equal(conversationHref(null), '#whatsapp');
    assert.equal(salesToolsHref('lead-1'), '#whatsapp/sales/lead-1');
    assert.deepEqual(whatsappTarget('#whatsapp/sales/lead-1'), { conversationId: null, salesLeadId: 'lead-1' });
    assert.deepEqual(whatsappTarget('#whatsapp/conv_1'), { conversationId: 'conv_1', salesLeadId: null });
    assert.deepEqual(whatsappTarget('#whatsapp/a%2Fb'), { conversationId: null, salesLeadId: null });
    assert.deepEqual(whatsappTarget('#business'), { conversationId: null, salesLeadId: null });
  });
});
