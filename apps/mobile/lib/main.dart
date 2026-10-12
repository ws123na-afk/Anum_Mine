import 'package:flutter/widgets.dart';

import 'src/anum_app.dart';
import 'src/font_licenses.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  registerFontLicenses();
  runApp(const AnumApp());
}
