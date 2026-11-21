"""
Email Service - Handles email verification codes and notifications.
"""
import os
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Tuple
from app.services.notifications.email_templates import render_verification_email
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Singleton instance
_email_service = None


def get_email_service():
    """Get singleton EmailService instance"""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service


class EmailService:
    """Email service for verification codes and notifications"""
    
    def __init__(self):
        self._load_config()
    
    def _load_config(self):
        """Load email configuration from environment variables"""
        self.smtp_host = os.getenv('SMTP_HOST', '')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = os.getenv('SMTP_USER', '')
        self.smtp_password = os.getenv('SMTP_PASSWORD', '')
        self.smtp_from = os.getenv('SMTP_FROM', '') or self.smtp_user
        self.smtp_use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
        self.smtp_use_ssl = os.getenv('SMTP_USE_SSL', 'false').lower() == 'true'
        
        # Verification code settings
        self.code_expire_minutes = int(os.getenv('VERIFICATION_CODE_EXPIRE_MINUTES', '10'))
        self.code_length = 6
        
        # Verification code attempt limits (anti-brute-force)
        self.code_max_attempts = int(os.getenv('VERIFICATION_CODE_MAX_ATTEMPTS', '5'))
        self.code_lock_minutes = int(os.getenv('VERIFICATION_CODE_LOCK_MINUTES', '30'))
        
        # Check if email is properly configured
        self.email_enabled = bool(self.smtp_host and self.smtp_user and self.smtp_password)
        
        if not self.email_enabled:
            logger.warning("Email service is not configured. SMTP settings are missing.")
    
    def is_configured(self) -> bool:
        """Check if email service is properly configured"""
        return self.email_enabled
    
    # =========================================================================
    # Verification Code Generation & Storage
    # =========================================================================
    
    def generate_code(self) -> str:
        """Generate a random numeric verification code"""
        return ''.join(random.choices(string.digits, k=self.code_length))
    
    def create_verification_code(self, email: str, code_type: str, 
                                  ip_address: str = None) -> Tuple[bool, str]:
        """
        Create and store a new verification code.
        
        Args:
            email: Email address
            code_type: Type of verification (register, reset_password, change_password, change_email)
            ip_address: Requester's IP address
        
        Returns:
            (success, code_or_message)
        """
        try:
            code = self.generate_code()
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Invalidate any existing unused codes of the same type for this email
                cur.execute(
                    """
                    UPDATE qd_verification_codes 
                    SET used_at = NOW() 
                    WHERE email = ? AND type = ? AND used_at IS NULL
                    """,
                    (email, code_type)
                )
                
                # Insert new code
                cur.execute(
                    """
                    INSERT INTO qd_verification_codes 
                    (email, code, type, expires_at, ip_address)
                    VALUES (
                        ?, ?, ?,
                        NOW() + make_interval(mins => ?),
                        ?
                    )
                    """,
                    (
                        email,
                        code,
                        code_type,
                        self.code_expire_minutes,
                        ip_address,
                    )
                )
                db.commit()
                cur.close()
            
            return True, code
            
        except Exception as e:
            logger.error(f"Failed to create verification code: {e}")
            return False, 'Failed to generate verification code'
    
    def verify_code(self, email: str, code: str, code_type: str) -> Tuple[bool, str]:
        """
        Verify a submitted code with brute-force protection.
        
        Args:
            email: Email address
            code: The code to verify
            code_type: Type of verification
        
        Returns:
            (valid, message)
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()

                cur.execute(
                    """
                    SELECT COALESCE(SUM(attempts), 0) AS failed_attempts
                    FROM qd_verification_codes
                    WHERE email = ? AND type = ?
                      AND last_attempt_at > NOW() - make_interval(mins => ?)
                    """,
                    (email, code_type, self.code_lock_minutes)
                )
                failure_row = cur.fetchone() or {}
                failed_attempts = int(failure_row.get('failed_attempts') or 0)
                if failed_attempts >= self.code_max_attempts:
                    db.rollback()
                    cur.close()
                    return False, f'Too many failed attempts. Please try again in {self.code_lock_minutes} minutes'
                
                # Lock the latest code so concurrent guesses cannot race the
                # attempt counter. Expiration is derived from created_at using
                # the same database clock that wrote it.
                cur.execute(
                    """
                    SELECT
                        id,
                        code as stored_code,
                        attempts,
                        created_at + make_interval(mins => ?) > NOW() AS is_valid
                    FROM qd_verification_codes
                    WHERE email = ? AND type = ? AND used_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (self.code_expire_minutes, email, code_type)
                )
                row = cur.fetchone()
                
                if not row:
                    db.rollback()
                    cur.close()
                    return False, 'Invalid verification code'
                
                code_id = row['id']
                stored_code = row['stored_code']
                attempts = int(row['attempts'] or 0)

                if attempts >= self.code_max_attempts:
                    db.rollback()
                    cur.close()
                    return False, f'Too many failed attempts. Please try again in {self.code_lock_minutes} minutes'

                if not row.get('is_valid'):
                    db.rollback()
                    cur.close()
                    return False, 'Verification code has expired'
                
                # Check if code matches
                if stored_code != code:
                    cur.execute(
                        """
                        UPDATE qd_verification_codes
                        SET attempts = COALESCE(attempts, 0) + 1,
                            last_attempt_at = NOW()
                        WHERE id = ?
                        RETURNING attempts
                        """,
                        (code_id,)
                    )
                    attempt_row = cur.fetchone() or {}
                    new_attempts = int(attempt_row.get('attempts') or (attempts + 1))
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(attempts), 0) AS failed_attempts
                        FROM qd_verification_codes
                        WHERE email = ? AND type = ?
                          AND last_attempt_at > NOW() - make_interval(mins => ?)
                        """,
                        (email, code_type, self.code_lock_minutes)
                    )
                    failure_row = cur.fetchone() or {}
                    failed_attempts = int(
                        failure_row.get('failed_attempts') or new_attempts
                    )
                    db.commit()
                    cur.close()
                    
                    remaining = self.code_max_attempts - failed_attempts
                    if remaining <= 0:
                        return False, f'Too many failed attempts. Please try again in {self.code_lock_minutes} minutes'
                    return False, f'Invalid verification code. {remaining} attempts remaining'
                
                # Mark as used
                cur.execute(
                    "UPDATE qd_verification_codes SET used_at = NOW() WHERE id = ?",
                    (code_id,)
                )
                db.commit()
                cur.close()
                
                return True, 'verified'
                
        except Exception as e:
            logger.error(f"Failed to verify code: {e}")
            return False, 'Verification failed'
    
    # =========================================================================
    # Email Sending
    # =========================================================================
    
    def send_email(self, to_email: str, subject: str, html_body: str, *, text_body: str = None) -> Tuple[bool, str]:
        """
        Send an email.
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML body content
        
        Returns:
            (success, message)
        """
        if not self.email_enabled:
            logger.warning(f"Email not sent (service disabled): {subject} to {to_email}")
            return False, 'Email service is not configured'
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.smtp_from
            msg['To'] = to_email
            
            if text_body is None:
                import re
                text_body = html_body.replace('<br>', '\n').replace('<br/>', '\n')
                text_body = re.sub('<[^<]+?>', '', text_body)
            
            part1 = MIMEText(text_body, 'plain', 'utf-8')
            part2 = MIMEText(html_body, 'html', 'utf-8')
            
            msg.attach(part1)
            msg.attach(part2)
            
            # Connect and send
            if self.smtp_use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                if self.smtp_use_tls:
                    server.starttls()
            
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_from, to_email, msg.as_string())
            server.quit()
            
            logger.info(f"Email sent successfully: {subject} to {to_email}")
            return True, 'sent'
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return False, 'Email authentication failed'
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False, 'Failed to send email'
        except Exception as e:
            logger.error(f"Email error: {e}")
            return False, 'Failed to send email'
    
    def send_verification_code(self, email: str, code_type: str,
                                ip_address: str = None, *, language: str = "en-US") -> Tuple[bool, str]:
        """
        Generate and send a verification code email.
        
        Args:
            email: Recipient email address
            code_type: Type of verification (register, reset_password, change_password)
            ip_address: Requester's IP address
        
        Returns:
            (success, message)
        """
        # Generate code
        success, code_or_msg = self.create_verification_code(email, code_type, ip_address)
        if not success:
            return False, code_or_msg
        
        subject, html_body, text_body = render_verification_email(
            code_or_msg, code_type, self.code_expire_minutes, language,
        )
        return self.send_email(email, subject, html_body, text_body=text_body)
    
    # =========================================================================
    # Email Validation
    # =========================================================================
    
    @staticmethod
    def is_valid_email(email: str) -> bool:
        """Basic email format validation"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
