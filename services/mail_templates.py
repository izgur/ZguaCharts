"""Small responsive, escaped HTML emails with a plain-text equivalent."""
from datetime import datetime
from html import escape


def document(title, body, footer):
    return '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f2f5f6;font-family:Arial,sans-serif;color:#18232b">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #dce4e7;border-radius:8px">
<tr><td style="padding:24px 28px;border-bottom:1px solid #e5eaed"><span style="display:inline-block;background:#127f72;color:#fff;padding:6px 10px;border-radius:4px;font-weight:bold">Z</span>
<span style="font-size:18px;font-weight:bold;margin-left:10px">ZguaCharts</span></td></tr>
<tr><td style="padding:28px"><h1 style="font-size:23px;line-height:1.3;margin:0 0 20px">''' + escape(title) + '''</h1>''' + body + '''</td></tr>
<tr><td style="padding:20px 28px;background:#f8fafb;font-size:12px;line-height:1.6;color:#586973;border-top:1px solid #e5eaed">''' + escape(footer) + '''</td></tr>
</table></td></tr></table></body></html>'''


def verification(link):
    text = "Confirm your email address:\n\n" + link + "\n\nThis link expires in 24 hours. Open it on the computer running ZguaCharts. If you did not register, ignore this email."
    url = escape(link, quote=True)
    body = '<p style="line-height:1.6">Your charts, layouts and settings are ready for a place of their own. Confirm your email address to finish creating your account.</p>'
    body += '<p style="margin:26px 0"><a href="' + url + '" style="display:inline-block;background:#127f72;color:#fff;padding:12px 18px;border-radius:4px;text-decoration:none;font-weight:bold">Confirm email address</a></p>'
    body += '<p style="font-size:13px;line-height:1.6;color:#586973">Valid for 24 hours. This is a local app: open the link on the computer running ZguaCharts.</p>'
    body += '<p style="font-size:12px;overflow-wrap:anywhere">Or copy this link into your browser:<br><a href="' + url + '">' + url + '</a></p>'
    return "Confirm your ZguaCharts account", text, document("Confirm your email", body, "If you did not create an account, ignore this email. No account is activated without confirmation.")


def chart_alert(alert, event):
    market = "{} / {} / {}".format(alert["symbol"], alert["source"], alert["timeframe"])
    stamp = datetime.utcfromtimestamp(event["time"]).strftime("%d %b %Y, %H:%M:%S UTC")
    conditions = []
    for condition in alert.get("conditions", []):
        target = condition.get("reference") or str(condition.get("value", ""))
        conditions.append("{} {} {}".format(condition["field"], condition["operator"].replace("-", " "), target))
    message = alert["message"]
    text = market + "\n" + stamp + "\n\n" + message + "\n\nConditions (" + alert.get("match", "all") + "):\n" + "\n".join(conditions)
    footer = "Signals are technical-analysis hints, not financial advice. This notification does not place any orders. Manage email notifications in your chart alerts."
    text += "\n\n" + footer
    body = '<p style="font-size:14px;color:#586973">' + escape(market) + '<br>' + escape(stamp) + '</p>'
    body += '<p style="font-size:18px;line-height:1.6">' + escape(message).replace("\n", "<br>") + '</p>'
    body += '<h2 style="font-size:14px;margin-top:24px">Alert conditions (' + escape(alert.get("match", "all")) + ')</h2><ul style="padding-left:20px;line-height:1.7;font-size:14px">'
    body += ''.join('<li>' + escape(item) + '</li>' for item in conditions) + '</ul>'
    return "ZguaCharts alert: " + market, text, document("Your chart alert triggered", body, footer)


def setup_test():
    text = "ZguaCharts email is connected.\n\nChart alerts and account confirmation emails can now use Resend. This is a setup test, not a market signal. No order has been placed.\n\nWithout a verified domain, delivery is limited to your Resend account email address."
    body = '<p style="font-size:16px;line-height:1.6">Your email connection is ready.</p><p style="line-height:1.6">ZguaCharts can now send:</p><ul style="line-height:1.8"><li>Price and indicator alerts</li><li>Account email confirmation and verification reminders</li></ul><p style="font-size:13px;line-height:1.6;color:#586973">This is a connection test, not a market signal. No order has been placed.</p>'
    return "ZguaCharts email is connected", text, document("A clear signal. In your inbox.", body, "Test mode: until a sender domain is verified, Resend can deliver only to the account owner's email address.")


def password_reset(link):
    text = "Reset your ZguaCharts password:\n\n" + link + "\n\nThis one-use link expires in one hour. Open it on the computer running ZguaCharts. If you did not request this, ignore this email. Your password has not changed."
    body = '<p>A password reset was requested for your account.</p><p style="margin:24px 0"><a style="display:inline-block;background:#127f72;color:#fff;padding:12px 18px;border-radius:4px;text-decoration:none" href="' + escape(link, quote=True) + '">Reset password</a></p><p style="font-size:13px;line-height:1.6">This link expires in one hour. Open it on the computer running ZguaCharts.</p>'
    return "Reset your ZguaCharts password", text, document("Reset your password", body, "If you did not request this, ignore this email. Your password has not changed.")
