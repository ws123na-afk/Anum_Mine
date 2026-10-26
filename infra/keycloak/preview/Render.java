// Renders the ANUM login theme's templates with sample values into static HTML, with FreeMarker
// alone (no Keycloak server). See preview.sh and docs/identity.md#login-theme-preview.
//
//   java -cp freemarker.jar Render.java <themes-dir> <anum-theme-dir> <out-dir>
//
// <themes-dir> holds Keycloak 26.4's own `base` and `keycloak.v2` login themes (the jar
// keycloak-themes-26.4.0.jar, folder theme/). Every page is rendered in English and Arabic with
// the template files of the anum theme first, then keycloak.v2, then base: the same order
// Keycloak resolves them. Output: <out>/<scenario>.<lang>.html next to res/ (the three themes'
// resources merged in the same order) and common/ (PatternFly, put there by preview.sh).
import freemarker.template.*;
import freemarker.cache.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.text.MessageFormat;
import java.util.*;

public class Render {
    static Properties load(Path... files) throws IOException {
        Properties p = new Properties();
        for (Path f : files) {
            if (!Files.exists(f)) continue;
            try (Reader r = Files.newBufferedReader(f, StandardCharsets.UTF_8)) { p.load(r); }
        }
        return p;
    }

    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    static class Scenario {
        final String name, template, pageId;
        final java.util.function.Consumer<Map<String, Object>> tweak;
        Scenario(String name, String template, String pageId, java.util.function.Consumer<Map<String, Object>> tweak) {
            this.name = name; this.template = template; this.pageId = pageId; this.tweak = tweak;
        }
    }

    static Map<String, Object> attr(String name, String display, String value, boolean required, String type, String autocomplete) {
        return map("name", name, "displayName", display, "value", value, "values", List.of(value), "required", required,
                "readOnly", false, "multivalued", false, "autocomplete", autocomplete, "group", "", "html5DataAnnotations", map(),
                "annotations", map("inputType", type), "validators", map());
    }

    public static void main(String[] args) throws Exception {
        Path themes = Paths.get(args[0]), anum = Paths.get(args[1]), out = Paths.get(args[2]);
        Path base = themes.resolve("base/login"), v2 = themes.resolve("keycloak.v2/login");
        Files.createDirectories(out);

        Configuration cfg = new Configuration(Configuration.VERSION_2_3_32);
        cfg.setTemplateLoader(new MultiTemplateLoader(new TemplateLoader[] {
            new FileTemplateLoader(anum.toFile()), new FileTemplateLoader(v2.toFile()), new FileTemplateLoader(base.toFile())}));
        cfg.setDefaultEncoding("UTF-8");
        cfg.setOutputFormat(freemarker.core.HTMLOutputFormat.INSTANCE);
        cfg.setOutputEncoding("UTF-8");
        cfg.setNumberFormat("computer");
        cfg.setTemplateExceptionHandler(TemplateExceptionHandler.RETHROW_HANDLER);

        Properties props = load(base.resolve("theme.properties"), v2.resolve("theme.properties"), anum.resolve("theme.properties"));
        // The realm's own settings win in Keycloak; the preview uses the theme's.
        Map<String, Object> properties = new HashMap<>();
        for (String k : props.stringPropertyNames()) properties.put(k, props.getProperty(k));

        List<Scenario> scenarios = new ArrayList<>();
        scenarios.add(new Scenario("login", "login.ftl", "login", m -> {
            ((Map<String, Object>) m.get("realm")).put("registrationAllowed", true);
            m.put("login", map("username", "", "rememberMe", false));
            m.put("social", map("providers", List.of(
                map("alias", "google", "displayName", "Google", "loginUrl", "#google", "iconClasses", ""),
                map("alias", "apple", "displayName", "Apple", "loginUrl", "#apple", "iconClasses", ""))));
        }));
        scenarios.add(new Scenario("login-plain", "login.ftl", "login", m -> {
            m.put("login", map("username", "", "rememberMe", false));
        }));
        scenarios.add(new Scenario("login-error", "login.ftl", "login", m -> {
            m.put("login", map("username", "sara@example.com", "rememberMe", false));
            m.put("message", map("type", "error", "summary", m.get("lang").equals("ar") ? "اسم المستخدم أو كلمة المرور غير صحيحة." : "Invalid username or password."));
            m.put("messagesPerField", fields(true));
            ((Map<String, Object>) m.get("realm")).put("registrationAllowed", true);
        }));
        scenarios.add(new Scenario("login-local", "login.ftl", "login", m -> {
            ((Map<String, Object>) m.get("realm")).put("displayName", "ANUM (local development)");
            m.put("login", map("username", "", "rememberMe", false));
        }));
        scenarios.add(new Scenario("login-info", "login.ftl", "login", m -> {
            m.put("login", map("username", "", "rememberMe", false));
            m.put("message", map("type", "success", "summary", m.get("lang").equals("ar") ? "تم تحديث كلمة المرور." : "Your password has been updated."));
        }));
        scenarios.add(new Scenario("reset-password", "login-reset-password.ftl", "reset-password", m -> {
            m.put("message", map("type", "info", "summary", ""));
            m.remove("message");
        }));
        scenarios.add(new Scenario("otp", "login-otp.ftl", "otp", m -> {
            m.put("otpLogin", map("userOtpCredentials", List.of(), "selectedCredentialId", ""));
            ((Map<String, Object>) m.get("auth")).put("attemptedUsername", "sara@example.com");
        }));
        scenarios.add(new Scenario("otp-error", "login-otp.ftl", "otp", m -> {
            m.put("otpLogin", map("userOtpCredentials", List.of(), "selectedCredentialId", ""));
            m.put("message", map("type", "error", "summary", m.get("lang").equals("ar") ? "رمز التحقق غير صالح." : "Invalid authenticator code."));
            m.put("messagesPerField", fields(true));
        }));
        scenarios.add(new Scenario("config-totp", "login-config-totp.ftl", "config-totp", m -> {
            m.put("totp", map("totpSecretEncoded", "JBSW Y3DP EHPK 3PXP", "totpSecretQrCode", qr(),
                "supportedApplications", List.of("totpAppFreeOTPName", "totpAppGoogleName"), "manualUrl", "#manual", "totpSecret", "JBSWY3DPEHPK3PXP", "otpCredentials", List.of(), "policy",
                map("supportedApplications", List.of("totpAppFreeOTPName", "totpAppGoogleName"), "algorithm", "HmacSHA1", "digits", 6, "period", 30, "lookAheadWindow", 1, "type", "totp", "getAlgorithmKey", "SHA1", "initialCounter", 0),
                "qrUrl", "#qr", "totpSecretQrCodeUrl", ""));
            m.put("isAppInitiatedAction", false);
            m.put("mode", "qr");
        }));
        scenarios.add(new Scenario("update-password", "login-update-password.ftl", "update-password", m -> {
            m.put("username", "sara@example.com");
            m.put("passwordPolicies", map("length", 12, "maxLength", 128, "lowerCase", 1, "upperCase", 1, "digits", 1, "specialChars", 0, "notUsername", true, "notEmail", true));
        }));
        scenarios.add(new Scenario("update-password-error", "login-update-password.ftl", "update-password", m -> {
            m.put("username", "sara@example.com");
            m.put("passwordPolicies", map("length", 12, "maxLength", 128, "lowerCase", 1, "upperCase", 1, "digits", 1, "specialChars", 0, "notUsername", true, "notEmail", true));
            m.put("message", map("type", "error", "summary", m.get("lang").equals("ar") ? "كلمة المرور الجديدة لا تطابق التأكيد." : "Passwords don't match."));
            m.put("messagesPerField", fields(true));
        }));
        scenarios.add(new Scenario("register", "register.ftl", "register", m -> {
            Map<String, Object> realm = (Map<String, Object>) m.get("realm");
            realm.put("registrationEmailAsUsername", true);
            m.put("passwordPolicies", map("length", 12, "maxLength", 128, "lowerCase", 1, "upperCase", 1, "digits", 1, "specialChars", 0, "notUsername", true, "notEmail", true));
            m.put("passwordRequired", true);
            m.put("recaptchaRequired", false);
            m.put("recaptchaSiteKey", "");
            m.put("recaptchaAction", "");
            m.put("termsAcceptanceRequired", false);
            boolean ar = m.get("lang").equals("ar");
            m.put("profile", map("attributes", List.of(
                attr("email", ar ? "البريد الإلكتروني" : "Email", "", true, "text", "email"),
                attr("firstName", ar ? "الاسم الأول" : "First name", "", true, "text", "given-name"),
                attr("lastName", ar ? "اسم العائلة" : "Last name", "", true, "text", "family-name")),
                "html5DataAnnotations", map(), "attributesByCategory", Map.of()));
            m.put("register", map("formData", map()));
        }));
        scenarios.add(new Scenario("logout-confirm", "logout-confirm.ftl", "logout-confirm", m -> {
            m.put("logoutConfirm", map("code", "x", "skipLink", false));
            m.put("client", map("baseUrl", "#", "name", "ANUM"));
        }));
        scenarios.add(new Scenario("error", "error.ftl", "error", m -> {
            m.put("message", map("type", "error", "summary", m.get("lang").equals("ar") ? "حدث خطأ غير متوقع. حاول مرة أخرى." : "An unexpected error occurred. Please try again."));
            m.put("client", map("baseUrl", "#", "name", "ANUM"));
            m.put("skipLink", false);
        }));
        scenarios.add(new Scenario("info", "info.ftl", "info", m -> {
            m.put("message", map("type", "info", "summary", m.get("lang").equals("ar") ? "أُرسل إلى بريدك رابط لإعادة تعيين كلمة المرور." : "An email with instructions to reset your password has been sent."));
            m.put("client", map("baseUrl", "#", "name", "ANUM"));
            m.put("messageHeader", "");
            m.put("requiredActions", List.of());
            m.put("skipLink", false);
            m.put("pageRedirectUri", "#");
            m.put("actionUri", "#");
        }));
        scenarios.add(new Scenario("verify-email", "login-verify-email.ftl", "verify-email", m -> {
            m.put("user", map("email", "sara@example.com"));
            m.put("message", map("type", "warning", "summary", m.get("lang").equals("ar") ? "عليك التحقق من بريدك الإلكتروني." : "You need to verify your email address to activate your account."));
        }));
        scenarios.add(new Scenario("page-expired", "login-page-expired.ftl", "page-expired", m -> {
            m.put("url", withUrl(m, "loginRestartFlowUrl", "#restart", "loginAction", "#continue"));
        }));

        for (String lang : List.of("en", "ar")) {
            Properties messages = load(base.resolve("messages/messages_" + lang + ".properties"), anum.resolve("messages/messages_" + lang + ".properties"));
            for (Scenario s : scenarios) {
                Map<String, Object> m = new HashMap<>();
                boolean rtl = lang.equals("ar");
                m.put("lang", lang);
                m.put("locale", map("rtl", rtl, "currentLanguageTag", lang,
                    "supported", List.of(map("label", "English", "url", "#en", "languageTag", "en"), map("label", "العربية", "url", "#ar", "languageTag", "ar"))));
                m.put("realm", map("displayName", "ANUM", "displayNameHtml", "ANUM", "internationalizationEnabled", true, "loginWithEmailAllowed", true,
                    "registrationEmailAsUsername", false, "password", true, "registrationAllowed", false, "resetPasswordAllowed", true, "rememberMe", true,
                    "duplicateEmailsAllowed", false, "editUsernameAllowed", false, "name", "anum"));
                m.put("url", map("loginAction", "#login", "resourcesPath", "res", "resourcesCommonPath", "common", "loginResetCredentialsUrl", "#reset",
                    "registrationUrl", "#register", "registrationAction", "#register", "loginUrl", "#login", "loginRestartFlowUrl", "#restart",
                    "ssoLoginInOtherTabsUrl", "#sso", "logoutConfirmAction", "#logout", "getFirstValue", "#"));
                m.put("auth", map("attemptedUsername", "", "selectedCredential", "",
                    "showUsername", (TemplateMethodModelEx) a -> false, "showResetCredentials", (TemplateMethodModelEx) a -> false,
                    "showTryAnotherWayLink", (TemplateMethodModelEx) a -> false, "authenticationSelections", List.of()));
                m.put("messagesPerField", fields(false));
                m.put("properties", properties);
                m.put("pageId", s.pageId);
                m.put("darkMode", true);
                m.put("msg", (TemplateMethodModelEx) a -> format(messages, a));
                m.put("advancedMsg", (TemplateMethodModelEx) a -> format(messages, a));
                m.put("kcSanitize", (TemplateMethodModelEx) a -> String.valueOf(a.get(0)));
                m.put("login", map("username", "", "rememberMe", false));
                m.put("social", map("providers", List.of()));
                s.tweak.accept(m);
                Template t = cfg.getTemplate(s.template);
                Path file = out.resolve(s.name + "." + lang + ".html");
                try (Writer w = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) { t.process(m, w); }
                catch (Exception e) { System.err.println("FAILED " + s.name + "." + lang + ": " + e.getMessage().lines().limit(3).reduce("", (x, y) -> x + " | " + y)); }
            }
        }
        System.out.println("rendered into " + out);
    }

    static Map<String, Object> withUrl(Map<String, Object> m, String... kv) {
        Map<String, Object> u = new LinkedHashMap<>((Map<String, Object>) m.get("url"));
        for (int i = 0; i < kv.length; i += 2) u.put(kv[i], kv[i + 1]);
        return u;
    }

    static Map<String, Object> fields(boolean error) {
        return map("existsError", (TemplateMethodModelEx) a -> error, "exists", (TemplateMethodModelEx) a -> error,
            "get", (TemplateMethodModelEx) a -> "", "getFirstError", (TemplateMethodModelEx) a -> "");
    }

    static Object format(Properties messages, List<?> a) {
        String key = String.valueOf(a.get(0));
        String pattern = messages.getProperty(key, key);
        Object[] rest = a.subList(1, a.size()).stream().map(String::valueOf).toArray();
        return new MessageFormat(pattern).format(rest);
    }

    static String qr() {
        try {
        // A stand-in QR code (random squares on white): enough to check the frame around the real one.
        java.awt.image.BufferedImage img = new java.awt.image.BufferedImage(200, 200, java.awt.image.BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 200; y++) for (int x = 0; x < 200; x++) img.setRGB(x, y, ((x / 8 + y / 8) >= 0 && new Random((x / 8) * 31L + (y / 8)).nextInt(3) == 0) ? 0 : 0xFFFFFF);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        javax.imageio.ImageIO.write(img, "png", bytes);
        return Base64.getEncoder().encodeToString(bytes.toByteArray());
        } catch (IOException e) { throw new UncheckedIOException(e); }
    }
}
