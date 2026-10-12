// Secure storage on iOS (lib/src/infrastructure/mobile_adapters.dart): the
// session and workspace choice are device-only keychain items, and items
// stored with the old class move over once instead of signing people out.
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

/// A keychain stand-in: an item's identity is its key, but a query only
/// matches an item of the same accessibility class, as on iOS.
class FakeKeychain implements FlutterSecureStorage {
  final items = <String, (KeychainAccessibility?, String)>{};

  KeychainAccessibility? _class(AppleOptions? options) =>
      (options ?? IOSOptions.defaultOptions).accessibility;

  @override
  Future<String?> read(
      {required String key,
      AppleOptions? iOptions,
      AndroidOptions? aOptions,
      LinuxOptions? lOptions,
      WebOptions? webOptions,
      AppleOptions? mOptions,
      WindowsOptions? wOptions}) async {
    final item = items[key];
    return item != null && item.$1 == _class(iOptions) ? item.$2 : null;
  }

  @override
  Future<void> write(
      {required String key,
      required String? value,
      AppleOptions? iOptions,
      AndroidOptions? aOptions,
      LinuxOptions? lOptions,
      WebOptions? webOptions,
      AppleOptions? mOptions,
      WindowsOptions? wOptions}) async {
    final existing = items[key];
    if (existing != null && existing.$1 != _class(iOptions)) {
      throw StateError('errSecDuplicateItem');
    }
    items[key] = (_class(iOptions), value!);
  }

  @override
  Future<void> delete(
      {required String key,
      AppleOptions? iOptions,
      AndroidOptions? aOptions,
      LinuxOptions? lOptions,
      WebOptions? webOptions,
      AppleOptions? mOptions,
      WindowsOptions? wOptions}) async {
    if (items[key]?.$1 == _class(iOptions)) items.remove(key);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

const _deviceOnly = KeychainAccessibility.first_unlock_this_device;

LocalSession _session() => LocalSession(
      accessToken: 'example',
      tokenType: 'bearer',
      expiresAt: DateTime.utc(2030),
      context: const TenantContext(
        tenantId: 'tenant_a',
        workspaceId: 'workspace_a',
        userId: 'user_a',
        roles: ['owner'],
      ),
    );

void main() {
  test('sessions are written as device-only keychain items', () async {
    final keychain = FakeKeychain();
    final store = SecureSessionStore(storage: keychain);
    await store.write(_session());
    expect(keychain.items['anum.local-session']?.$1, _deviceOnly);
    expect((await store.read())?.context.tenantId, 'tenant_a');
  });

  test('a session stored with the old class moves over once', () async {
    final keychain = FakeKeychain();
    await SecureSessionStore(storage: keychain).write(_session());
    // Rewrite it as the plugin default (WhenUnlocked), as older builds did.
    final value = keychain.items['anum.local-session']!.$2;
    keychain.items['anum.local-session'] =
        (KeychainAccessibility.unlocked, value);

    final store = SecureSessionStore(storage: keychain);
    expect((await store.read())?.context.userId, 'user_a');
    expect(keychain.items['anum.local-session']?.$1, _deviceOnly);
  });

  test('sign-out removes a session of either class', () async {
    final keychain = FakeKeychain();
    final store = SecureSessionStore(storage: keychain);
    await store.write(_session());
    await store.clear();
    expect(keychain.items, isEmpty);

    keychain.items['anum.local-session'] =
        (KeychainAccessibility.unlocked, 'old');
    await store.clear();
    expect(keychain.items, isEmpty);
  });

  test('the workspace choice is device-only and migrates too', () async {
    final keychain = FakeKeychain();
    final store = SecureWorkspacePreferenceStore(storage: keychain);
    await store.write(tenantId: 't', userId: 'u', workspaceId: 'w1');
    expect(keychain.items.values.single.$1, _deviceOnly);

    keychain.items
        .updateAll((_, item) => (KeychainAccessibility.unlocked, 'w2'));
    expect(await store.read(tenantId: 't', userId: 'u'), 'w2');
    expect(keychain.items.values.single.$1, _deviceOnly);
    expect(await store.read(tenantId: 'other', userId: 'u'), isNull);
  });
}
