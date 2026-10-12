"""Preview email setup safely; --send explicitly delivers one test to configured recipient."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import account_mail, mail_templates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send one setup email to the configured alert recipient")
    parser.add_argument("--preview", action="store_true", help="Save sample HTML emails under reports/mail-previews without sending")
    args = parser.parse_args()
    try:
        config = account_mail.configuration()
        if args.preview:
            directory = Path(__file__).resolve().parents[1] / "reports" / "mail-previews"
            directory.mkdir(parents=True, exist_ok=True)
            alert = {"symbol": "BTCUSDT.P", "source": "bybit", "timeframe": "1h", "message": "Bitcoin crossed above your price level.",
                     "conditions": [{"field": "price", "operator": "crossing-up", "value": 86000}], "match": "all"}
            templates = {"setup": mail_templates.setup_test(),
                         "verification": mail_templates.verification("http://127.0.0.1:5000/charts#verify-email=preview-not-valid"),
                         "alert": mail_templates.chart_alert(alert, {"time": 1791288000})}
            for name, template in templates.items():
                (directory / (name + ".html")).write_text(template[2], encoding="utf-8")
            print("Sample emails saved under reports/mail-previews. Preview links are not valid tokens.")
            if not args.send:
                return 0
        if not account_mail.configured(config) or not config.get("to"):
            raise account_mail.MailDeliveryError("Configure outbound mail and alert recipient first")
        subject, text, html = mail_templates.setup_test()
        if not args.send:
            print("Mail configuration ready. No message sent. Use --send to send one setup email.")
            return 0
        result = account_mail.send(config["to"], subject, text, html=html, config=config)
        print("Email accepted by {}. Inbox delivery is not yet confirmed.".format(result["provider"]))
        return 0
    except account_mail.MailDeliveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("Mail setup failed. Check local configuration and network; no credentials were logged.", file=sys.stderr)
        return 1


if __name__ == "__main__": sys.exit(main())
