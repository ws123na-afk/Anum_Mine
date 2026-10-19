import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';
import 'invoice_controller.dart';
import 'invoice_models.dart';
import 'invoices.dart' show friendly, parseNumber, plainNumber;

/// The billing profile (docs/business.md#billing-profile): who the invoices
/// come from and how they are paid. Owners edit it; members read it. A sent
/// invoice keeps the profile it was sent with.
class BillingProfileScreen extends StatefulWidget {
  const BillingProfileScreen({required this.controller, super.key});
  final InvoicesController controller;

  @override
  State<BillingProfileScreen> createState() => _BillingProfileState();
}

class _BillingProfileState extends State<BillingProfileScreen> {
  final name = TextEditingController(),
      address = TextEditingController(),
      taxLabel = TextEditingController(),
      taxNumber = TextEditingController(),
      email = TextEditingController(),
      phone = TextEditingController(),
      bank = TextEditingController(),
      footer = TextEditingController(),
      prefix = TextEditingController(),
      dueDays = TextEditingController(),
      taxRate = TextEditingController();
  bool filled = false, busy = false;
  String? error, notice;
  Map<String, String> problems = {};

  InvoicesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await c.loadProfile();
      _fill();
    });
  }

  @override
  void dispose() {
    for (final t in [
      name,
      address,
      taxLabel,
      taxNumber,
      email,
      phone,
      bank,
      footer,
      prefix,
      dueDays,
      taxRate
    ]) {
      t.dispose();
    }
    super.dispose();
  }

  void _fill() {
    final p = c.profile;
    if (p == null || filled || !mounted) return;
    name.text = p.legalName;
    address.text = p.address;
    taxLabel.text = p.taxLabel;
    taxNumber.text = p.taxNumber;
    email.text = p.email;
    phone.text = p.phone;
    bank.text = p.bankDetails;
    footer.text = p.footerNote;
    prefix.text = p.invoicePrefix;
    dueDays.text = '${p.defaultDueDays}';
    taxRate.text = plainNumber(p.defaultTaxRatePct);
    setState(() => filled = true);
  }

  Future<void> _save() async {
    final l = context.anum;
    final days = int.tryParse(dueDays.text.trim());
    final rate = parseNumber(taxRate.text);
    final next = <String, String>{
      if (!RegExp(r'^[A-Za-z0-9]{1,8}$').hasMatch(prefix.text.trim()))
        'prefix': l.t('biz.inv.profile.prefixInvalid'),
      if (days == null || days < 0 || days > 365)
        'days': l.t('biz.inv.profile.daysInvalid'),
      if (rate == null || rate < 0 || rate > 100)
        'rate': l.t('biz.inv.profile.rateInvalid'),
    };
    setState(() {
      problems = next;
      error = null;
      notice = null;
    });
    if (next.isNotEmpty) return;
    setState(() => busy = true);
    try {
      await c.saveProfile(BillingProfile(
        legalName: name.text.trim(),
        address: address.text.trim(),
        taxLabel: taxLabel.text.trim(),
        taxNumber: taxNumber.text.trim(),
        email: email.text.trim(),
        phone: phone.text.trim(),
        bankDetails: bank.text.trim(),
        footerNote: footer.text.trim(),
        invoicePrefix: prefix.text.trim(),
        defaultDueDays: days!,
        defaultTaxRatePct: rate!,
      ));
      if (mounted) setState(() => notice = l.t('biz.inv.profile.saved'));
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _field(String key, TextEditingController controller, String label,
      {int lines = 1,
      int? max,
      TextInputType? type,
      String? problem,
      bool editable = true}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        key: Key('profile-$key'),
        controller: controller,
        readOnly: !editable,
        minLines: lines,
        maxLines: lines == 1 ? 1 : lines + 2,
        maxLength: editable ? max : null,
        keyboardType: type,
        decoration: InputDecoration(labelText: label, errorText: problem),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final editable = c.canManage;
        return Scaffold(
          appBar: AppBar(title: Text(l.t('biz.inv.profile'))),
          body: AnumPage(
            children: [
              switch (c.profileState) {
                BusinessLoad.idle || BusinessLoad.loading => AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('biz.loading')),
                BusinessLoad.denied => AnumFeedback(
                    kind: AnumFeedbackKind.permission,
                    message: c.profileError ?? l.t('biz.denied')),
                BusinessLoad.error => AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message:
                        l.f('biz.failed', {'message': c.profileError ?? ''}),
                    onRetry: () async {
                      await c.loadProfile();
                      _fill();
                    }),
                BusinessLoad.ready => Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(
                          editable
                              ? l.t('biz.inv.profile.intro')
                              : l.t('biz.inv.profile.readOnly'),
                          style: TextStyle(color: context.palette.muted)),
                      const SizedBox(height: 16),
                      _field('name', name, l.t('biz.inv.profile.legalName'),
                          max: 200, editable: editable),
                      _field('address', address, l.t('biz.inv.profile.address'),
                          lines: 2, max: 1000, editable: editable),
                      _field('taxLabel', taxLabel,
                          l.t('biz.inv.profile.taxLabel'),
                          max: 60, editable: editable),
                      _field('taxNumber', taxNumber,
                          l.t('biz.inv.profile.taxNumber'),
                          max: 60, editable: editable),
                      _field('email', email, l.t('biz.inv.profile.email'),
                          max: 254,
                          type: TextInputType.emailAddress,
                          editable: editable),
                      _field('phone', phone, l.t('biz.inv.profile.phone'),
                          max: 40,
                          type: TextInputType.phone,
                          editable: editable),
                      _field('bank', bank, l.t('biz.inv.profile.bank'),
                          lines: 3, max: 1500, editable: editable),
                      _field('footer', footer, l.t('biz.inv.profile.footer'),
                          max: 500, editable: editable),
                      _field('prefix', prefix, l.t('biz.inv.profile.prefix'),
                          max: 8,
                          problem: problems['prefix'],
                          editable: editable),
                      _field('days', dueDays, l.t('biz.inv.profile.days'),
                          type: TextInputType.number,
                          problem: problems['days'],
                          editable: editable),
                      _field('rate', taxRate, l.t('biz.inv.profile.rate'),
                          type: const TextInputType.numberWithOptions(
                              decimal: true),
                          problem: problems['rate'],
                          editable: editable),
                      if (error != null)
                        Text(error!,
                            key: const Key('profile-error'),
                            style: TextStyle(color: context.palette.stop)),
                      if (notice != null)
                        Text(notice!,
                            key: const Key('profile-notice'),
                            style: TextStyle(color: context.palette.ok)),
                      if (editable)
                        Align(
                          alignment: AlignmentDirectional.centerStart,
                          child: FilledButton(
                            key: const Key('profile-save'),
                            onPressed: busy ? null : _save,
                            child: Text(l.t('biz.inv.profile.save')),
                          ),
                        ),
                    ],
                  ),
              },
            ],
          ),
        );
      },
    );
  }
}
