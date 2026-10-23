import 'package:flutter/material.dart';

import '../../data/api_client.dart';

/// Sales leads use the existing authenticated ANUM API and workspace RLS.
class MobileSalesLead {
  MobileSalesLead(this.raw);
  final Map<String, dynamic> raw;
  String get id => raw['id'] as String? ?? '';
  String get company => raw['company_name'] as String? ?? '';
  String get name => raw['contact_name'] as String? ?? '';
  String get contactId => raw['contact_id'] as String? ?? '';
  String get stage => raw['status'] as String? ?? 'new';
  String get intent => raw['intent'] as String? ?? 'other';
  String get campaign => raw['campaign_hint'] as String? ?? '';
  String get requirements => raw['requirements'] as String? ?? '';
  String get currency => raw['currency'] as String? ?? 'SAR';
  String get phone => raw['phone_e164'] as String? ?? '';
  String get owner => raw['assigned_to'] as String? ?? '';
  String get budgetMin => raw['budget_min']?.toString() ?? '';
  String get budgetMax => raw['budget_max']?.toString() ?? '';
  int get revision => raw['revision'] as int? ?? 1;
  bool get marketingConsent => raw['marketing_consent'] == true;
}

class MobileSalesRepository {
  const MobileSalesRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/sales';

  Future<List<MobileSalesLead>> leads() async {
    final payload = await api.request('GET', '$base/leads?limit=100');
    final items = (payload['items'] as List?) ?? [];
    return items.whereType<Map>().map((item) =>
        MobileSalesLead(Map<String, dynamic>.from(item))).toList();
  }

  Future<Map<String, dynamic>> overview() async =>
      Map<String, dynamic>.from(await api.request('GET', '$base/overview'));

  Future<void> edit(MobileSalesLead lead, Map<String, dynamic> values) async {
    await api.request('PATCH', '$base/leads/${Uri.encodeComponent(lead.id)}',
        body: {'expected_revision': lead.revision, ...values});
  }

  Future<void> transition(MobileSalesLead lead, String stage) async {
    await api.request('POST', '$base/leads/${Uri.encodeComponent(lead.id)}/transition',
        body: {'expected_revision': lead.revision, 'status': stage});
  }

  Future<void> quotation(MobileSalesLead lead, String description,
      String price, String terms) async {
    await api.request('POST', '$base/leads/${Uri.encodeComponent(lead.id)}/quotes',
        body: {'items': [{'description': description, 'amount': price}],
          'currency': lead.currency, 'terms': terms});
  }

  Future<void> followUp(MobileSalesLead lead, DateTime due,
      String channel, String purpose) async {
    await api.request('POST', '$base/leads/${Uri.encodeComponent(lead.id)}/followups',
        body: {'due_at': due.toUtc().toIso8601String(),
          'channel': channel, 'purpose': purpose});
  }
}

const _stages = <String>['new', 'qualifying', 'qualified', 'quoted',
  'negotiating', 'won', 'lost', 'disqualified'];

/// Full mobile CRM workspace, linked from the WhatsApp inbox without changing
/// existing WhatsApp approvals, role permissions or Meta configuration.
class MobileSalesScreen extends StatefulWidget {
  const MobileSalesScreen({required this.repository, super.key});
  final MobileSalesRepository repository;

  @override
  State<MobileSalesScreen> createState() => _MobileSalesScreenState();
}

class _MobileSalesScreenState extends State<MobileSalesScreen> {
  List<MobileSalesLead> _leads = const [];
  Map<String, dynamic> _metrics = const {};
  String _search = '';
  String _stage = 'all';
  String? _error;
  bool _loading = true;
  bool _busy = false;

  bool get ar => Localizations.localeOf(context).languageCode == 'ar';
  String tr(String en, String arabic) => ar ? arabic : en;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() { _loading = true; _error = null; });
    try {
      final leads = await widget.repository.leads();
      final metrics = await widget.repository.overview();
      if (mounted) setState(() { _leads = leads; _metrics = metrics; });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.readable);
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _run(Future<void> Function() command) async {
    setState(() => _busy = true);
    try {
      await command();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:
          Text(tr('Saved. Messages and quotations still require approval.',
                  'تم الحفظ. الرسائل وعروض الأسعار ما زالت تتطلب الموافقة.'))));
      await _load();
    } on ApiException catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error.readable)));
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(error.toString())));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _leads.where((lead) {
      if (_stage != 'all' && lead.stage != _stage) return false;
      final needle = _search.toLowerCase();
      return [lead.name, lead.company, lead.phone, lead.campaign, lead.owner,
        lead.intent].join(' ').toLowerCase().contains(needle);
    }).toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Sales CRM', 'إدارة المبيعات')),
        actions: [IconButton(
          tooltip: tr('Refresh', 'تحديث'), icon: const Icon(Icons.refresh),
          onPressed: _busy ? null : _load,
        )],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          Text(tr('Marketing → WhatsApp → Sales', 'التسويق ← واتساب ← المبيعات'),
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            _stat(tr('Leads', 'العملاء'), 'leads'),
            _stat(tr('Qualified', 'مؤهلون'), 'qualified'),
            _stat(tr('Quoted', 'عروض الأسعار'), 'quoted'),
            _stat(tr('Won', 'ناجح'), 'won'),
            _stat(tr('Lost', 'مفقود'), 'lost'),
          ]),
          const SizedBox(height: 12),
          Text(tr('Showing up to 100 recent leads; totals may exclude older records.',
              'عرض آخر 100 عميل كحد أقصى، وقد لا تشمل الأرقام السجلات الأقدم.'),
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 14),
          TextField(
            decoration: InputDecoration(
                prefixIcon: const Icon(Icons.search),
                labelText: tr('Search name, company, phone', 'بحث بالاسم أو الشركة أو الهاتف')),
            onChanged: (value) => setState(() => _search = value),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            value: _stage,
            decoration: InputDecoration(labelText: tr('Sales stage', 'مرحلة المبيعات')),
            items: ['all', ..._stages].map((value) =>
                DropdownMenuItem(value: value, child: Text(value == 'all'
                    ? tr('All stages', 'جميع المراحل') : value))).toList(),
            onChanged: (value) => setState(() => _stage = value ?? 'all'),
          ),
          if (_loading) const Padding(padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator())),
          if (_error != null) Padding(padding: const EdgeInsets.all(16),
              child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error))),
          if (!_loading && filtered.isEmpty)
            Padding(padding: const EdgeInsets.all(24),
              child: Text(tr('No matching leads yet', 'لا يوجد عملاء مطابقون'))),
          for (final lead in filtered) Card(
            margin: const EdgeInsets.symmetric(vertical: 5),
            child: ListTile(
              key: Key('sales-lead-${lead.id}'),
              title: Text(lead.company.isNotEmpty ? lead.company :
                lead.name.isNotEmpty ? lead.name : lead.contactId),
              subtitle: Text('${lead.intent} · ${lead.stage}'
                  '${lead.campaign.isEmpty ? '' : ' · ${lead.campaign}'}'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () async {
                await Navigator.push<void>(context, MaterialPageRoute(
                  builder: (_) => _SalesLeadScreen(
                    repository: widget.repository, lead: lead)));
                if (mounted) await _load();
              },
            ),
          ),
        ]),
      ),
    );
  }

  Widget _stat(String label, String key) => SizedBox(
    width: 132,
    child: Card(child: Padding(padding: const EdgeInsets.all(12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.labelSmall),
        Text('${_metrics[key] ?? 0}', style: Theme.of(context).textTheme.headlineMedium),
      ]))),
  );
}

class _SalesLeadScreen extends StatefulWidget {
  const _SalesLeadScreen({required this.repository, required this.lead});
  final MobileSalesRepository repository;
  final MobileSalesLead lead;
  @override
  State<_SalesLeadScreen> createState() => _SalesLeadScreenState();
}

class _SalesLeadScreenState extends State<_SalesLeadScreen> {
  late final company = TextEditingController(text: widget.lead.company);
  late final name = TextEditingController(text: widget.lead.name);
  late final requirements = TextEditingController(text: widget.lead.requirements);
  late final owner = TextEditingController(text: widget.lead.owner);
  late final budgetMin = TextEditingController(text: widget.lead.budgetMin);
  late final budgetMax = TextEditingController(text: widget.lead.budgetMax);
  final description = TextEditingController();
  final price = TextEditingController();
  final terms = TextEditingController();
  final purpose = TextEditingController();
  late String stage = widget.lead.stage;
  String channel = 'whatsapp';
  DateTime? due;
  bool busy = false;
  bool get ar => Localizations.localeOf(context).languageCode == 'ar';
  String tr(String en,String arabic) => ar ? arabic : en;

  @override
  void dispose() {
    for (final controller in [company,name,requirements,owner,budgetMin,budgetMax,
      description,price,terms,purpose]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> run(Future<void> Function() action) async {
    setState(() => busy = true);
    try {
      await action();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:
        Text(tr('Saved. Approval required for outgoing actions.',
                'تم الحفظ. يلزم اعتماد الإجراءات الصادرة.'))));
      Navigator.pop(context);
    } on ApiException catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(error.readable)));
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(error.toString())));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget field(String label, TextEditingController controller,
      {int lines=1,TextInputType? keyboard}) =>
      Padding(padding: const EdgeInsets.symmetric(vertical: 6),
          child: TextField(controller: controller, maxLines: lines,
            keyboardType: keyboard, decoration: InputDecoration(labelText: label)));

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(widget.lead.company.isNotEmpty ?
      widget.lead.company : tr('Lead details', 'تفاصيل العميل'))),
    body: ListView(padding: const EdgeInsets.all(16), children: [
      Text(widget.lead.intent, style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 8),
      Text('${tr('Campaign', 'الحملة')}: ${widget.lead.campaign.isEmpty ? '-' : widget.lead.campaign}'),
      Text('${tr('Phone', 'الهاتف')}: ${widget.lead.phone}'),
      Text(widget.lead.marketingConsent
          ? tr('Marketing consent verified', 'تم تأكيد موافقة التسويق')
          : tr('Marketing consent not verified', 'موافقة التسويق غير مؤكدة')),
      field(tr('Company', 'الشركة'), company),
      field(tr('Contact', 'اسم العميل'), name),
      field(tr('Requirements', 'المتطلبات'), requirements, lines: 3),
      field(tr('Assigned user ID', 'معرف الموظف المسؤول'), owner),
      field(tr('Budget min', 'الميزانية من'), budgetMin, keyboard: TextInputType.number),
      field(tr('Budget max', 'الميزانية إلى'), budgetMax, keyboard: TextInputType.number),
      FilledButton(
        onPressed: busy ? null : () => run(() => widget.repository.edit(widget.lead, {
          'company_name': company.text, 'contact_name': name.text,
          'requirements': requirements.text,
          if (owner.text.trim().isNotEmpty) 'assigned_to': owner.text.trim(),
          if (budgetMin.text.trim().isNotEmpty) 'budget_min': budgetMin.text.trim(),
          if (budgetMax.text.trim().isNotEmpty) 'budget_max': budgetMax.text.trim(),
        })),
        child: Text(tr('Save lead', 'حفظ العميل')),
      ),
      const Divider(height: 32),
      DropdownButtonFormField<String>(
        value: stage, decoration: InputDecoration(labelText: tr('Stage','المرحلة')),
        items: _stages.map((value) => DropdownMenuItem(
          value:value,child:Text(value))).toList(),
        onChanged: (value) => setState(() => stage=value??stage),
      ),
      OutlinedButton(onPressed: busy || stage == widget.lead.stage ? null :
        () => run(() => widget.repository.transition(widget.lead,stage)),
        child: Text(tr('Change stage','تغيير المرحلة'))),
      const Divider(height: 32),
      Text(tr('Quotation draft', 'مسودة عرض سعر'),
        style: Theme.of(context).textTheme.titleMedium),
      Text(tr('Not sent until approval', 'لا يرسل قبل الموافقة')),
      field(tr('Description', 'وصف الخدمة'), description),
      field(tr('Amount', 'المبلغ'), price, keyboard: TextInputType.number),
      field(tr('Terms', 'الشروط'), terms, lines:2),
      FilledButton(onPressed: busy ? null : () {
        if (description.text.trim().isEmpty || price.text.trim().isEmpty) return;
        run(() => widget.repository.quotation(widget.lead, description.text, price.text, terms.text));
      },
        child: Text(tr('Save quotation draft','حفظ مسودة عرض السعر'))),
      const Divider(height: 32),
      Text(tr('Follow-up proposal','اقتراح متابعة'),
        style: Theme.of(context).textTheme.titleMedium),
      ListTile(title: Text(due == null ? tr('Choose date','اختر التاريخ') :
        due!.toLocal().toString()), trailing: const Icon(Icons.calendar_today),
        onTap: () async {
          final day = await showDatePicker(context: context,
            initialDate: DateTime.now().add(const Duration(days:1)),
            firstDate: DateTime.now(), lastDate: DateTime.now().add(const Duration(days:365)));
          if (!mounted || day == null) return;
          final time = await showTimePicker(context: context,initialTime: const TimeOfDay(hour:10, minute:0));
          if (time != null) setState(() => due = DateTime(day.year,day.month,day.day,time.hour,time.minute));
        }),
      DropdownButtonFormField<String>(value:channel,items: const [
        DropdownMenuItem(value:'whatsapp',child:Text('WhatsApp')),
        DropdownMenuItem(value:'email',child:Text('Email')),
        DropdownMenuItem(value:'call',child:Text('Call')),
      ],onChanged:(value)=>setState(()=>channel=value??channel)),
      field(tr('Reason','سبب المتابعة'),purpose),
      FilledButton(onPressed:busy ? null : () {
        if (due == null || purpose.text.trim().isEmpty) return;
        run(() => widget.repository.followUp(widget.lead, due!, channel, purpose.text));
      },
        child:Text(tr('Save follow-up proposal','حفظ اقتراح المتابعة'))),
    ]),
  );
}
