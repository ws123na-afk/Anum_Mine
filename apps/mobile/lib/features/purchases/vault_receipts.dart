import '../vault/vault_data.dart';
import '../vault/vault_transfer.dart';
import 'purchases_data.dart';

/// Receipts from the person's document vault (docs/purchases.md#receipts):
/// search the documents they can open, or pick a photo or PDF on the phone
/// and keep it in the vault as a private receipt.
class VaultPurchaseReceipts implements PurchaseReceipts {
  const VaultPurchaseReceipts(this.vault, {this.picker = pickVaultFile});
  final VaultRepository vault;
  final VaultFilePicker picker;

  static const extensions = ['.pdf', '.jpg', '.jpeg', '.png', '.heic', '.webp'];

  @override
  Future<List<ReceiptChoice>> search(String query) async => [
        for (final doc in (await vault.list(query: query)).items)
          ReceiptChoice(id: doc.id, title: doc.title, category: doc.category)
      ];

  @override
  Future<ReceiptChoice?> upload() async {
    final picked = await picker(extensions);
    if (picked == null) return null;
    final (path, name, _) = picked;
    final dot = name.lastIndexOf('.');
    final doc = await vault.upload(path,
        name: name,
        title: dot > 0 ? name.substring(0, dot) : name,
        category: 'receipt',
        accessClass: 'private');
    return ReceiptChoice(id: doc.id, title: doc.title, category: doc.category);
  }
}
