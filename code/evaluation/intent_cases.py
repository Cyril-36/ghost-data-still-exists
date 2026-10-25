"""SYNTHETIC multilingual intent evaluation set (team-written; not used in any prompt).

Thirteen scenario types in each of six languages. `expected` is the final authorization
the system should reach, including its scope: 'DELETE/ACCOUNT', 'DELETE/ITEM/<category>',
'NON_DELETION' or 'UNRESOLVED' ('|' separates acceptable alternatives); `label` is the intended Jev category. Contradictory or
hypothetical requests must stay UNRESOLVED, and a one-item request must never become an
account deletion: both are unsafe. M_item_keep_account was added after the audit of
e0aaef4 and is listed last so the earlier cases keep their cached batch composition.
"""

ACCOUNT, ITEM = 'DELETE/ACCOUNT', 'DELETE/ITEM/'
SCENARIOS = {
    'A_delete_account': (ACCOUNT, 'DELETE_ACCOUNT'),
    'B_verify_prior': (ACCOUNT, 'VERIFY_PRIOR_DELETION'),
    'C_scoped_item': (ITEM, 'SCOPED_DELETION'),          # target per language: ITEM_TARGETS
    'D_marketing_only': ('NON_DELETION', 'NON_DELETION'),
    'E_export_keep': ('NON_DELETION', 'NON_DELETION'),
    'F_correction': ('NON_DELETION', 'NON_DELETION'),
    'G_login_keep': ('NON_DELETION', 'NON_DELETION'),
    'H_double_negative_delete': (ACCOUNT, 'DELETE_ACCOUNT'),
    'I_double_negative_keep': ('NON_DELETION', 'NON_DELETION'),
    'J_hypothetical': ('UNRESOLVED', 'AMBIGUOUS'),
    'K_contradictory': ('UNRESOLVED', 'AMBIGUOUS'),
    'L_delete_with_legal_retention': (ACCOUNT, 'DELETE_ACCOUNT'),
    'M_item_keep_account': (ITEM, 'SCOPED_DELETION'),
}
ITEM_TARGETS = {
    'C_scoped_item': {'en': 'USER_EXPORT', 'es': 'USER_EXPORT', 'pt': 'SEARCH_INDEX_ENTRY', 'fr': 'SUPPORT_ATTACHMENT',
                      'ko': 'USER_EXPORT', 'ja': 'SUPPORT_ATTACHMENT'},
    'M_item_keep_account': {'en': 'USER_EXPORT', 'es': 'SUPPORT_ATTACHMENT', 'pt': 'SEARCH_INDEX_ENTRY',
                            'fr': 'USER_EXPORT', 'ko': 'SUPPORT_ATTACHMENT', 'ja': 'SEARCH_INDEX_ENTRY'},
}

TEXTS = {
    'en': {
        'A_delete_account': "I'd like you to permanently erase my account along with all the personal information you store about me.",
        'B_verify_prior': "Last month I asked for my profile to be deleted and got a confirmation. Can you check that nothing of mine is still stored and finish the job if needed?",
        'C_scoped_item': "My account was closed weeks ago, but a downloadable copy of my data is still accessible. Please get rid of that file.",
        'D_marketing_only': "Please take me off your newsletter and promotional mailing lists. That is all I need.",
        'E_export_keep': "Could you email me a copy of all the data linked to my account? I plan to keep using the shop.",
        'F_correction': "My phone number changed. Please update it on my profile.",
        'G_login_keep': "I can't sign in after resetting my password. Please help me get back in; I don't want the account closed.",
        'H_double_negative_delete': "It's not that I want to keep this account, I don't. Please delete it and my data.",
        'I_double_negative_keep': "I never asked anyone to remove my account, and I don't want it removed now.",
        'J_hypothetical': "If I were to close my account, would my past orders and invoices disappear too?",
        'K_contradictory': "Delete my account, but keep my profile visible so my family can still use my saved addresses.",
        'L_delete_with_legal_retention': "Please delete my account and personal data. I understand you may have to keep tax invoices for legal reasons.",
    },
    'es': {
        'A_delete_account': "Quiero que borren de forma definitiva mi cuenta y todos los datos personales que tengan sobre mí.",
        'B_verify_prior': "Hace un mes solicité eliminar mi perfil y recibí la confirmación. ¿Pueden comprobar que no queda nada mío y terminar el proceso si hace falta?",
        'C_scoped_item': "Mi cuenta ya está cerrada, pero el enlace a mi archivo de exportación sigue funcionando. Por favor, eliminen ese archivo.",
        'D_marketing_only': "Dejen de enviarme correos promocionales, por favor. Solo quiero darme de baja del boletín.",
        'E_export_keep': "¿Me pueden enviar una copia de mis datos? Sigo usando mi cuenta y no quiero perderla.",
        'F_correction': "He cambiado de dirección; por favor, actualicen mis datos de envío.",
        'G_login_keep': "No puedo iniciar sesión. Ayúdenme a recuperar el acceso, pero no cierren mi cuenta.",
        'H_double_negative_delete': "No es que quiera conservar la cuenta; al contrario. Elimínenla junto con mis datos.",
        'I_double_negative_keep': "Yo nunca pedí que borraran mi cuenta y no quiero que la borren.",
        'J_hypothetical': "Si cierro mi cuenta, ¿perdería también el historial de pedidos?",
        'K_contradictory': "Eliminen mi cuenta, pero mantengan activo mi perfil para que mi hermana pueda seguir usándolo.",
        'L_delete_with_legal_retention': "Por favor, eliminen mi cuenta y mis datos personales. Entiendo que deban conservar las facturas por obligaciones fiscales.",
    },
    'pt': {
        'A_delete_account': "Peço que excluam definitivamente a minha conta e todos os meus dados pessoais.",
        'B_verify_prior': "Pedi a exclusão do meu perfil no mês passado e recebi a confirmação. Podem verificar se ainda resta algum dado meu e concluir a exclusão, se necessário?",
        'C_scoped_item': "A minha conta já foi encerrada, mas ainda encontro meu perfil antigo na busca do site. Removam esse resultado, por favor.",
        'D_marketing_only': "Não quero mais receber e-mails de marketing. Só quero cancelar a newsletter.",
        'E_export_keep': "Gostaria de receber uma cópia dos meus dados. Continuo usando a conta normalmente.",
        'F_correction': "Meu sobrenome está escrito errado no cadastro. Podem corrigir?",
        'G_login_keep': "Esqueci minha senha e não consigo entrar. Me ajudem a recuperar o acesso; não encerrem a conta.",
        'H_double_negative_delete': "Não é que eu queira manter a conta. Quero que ela seja apagada, com todos os meus dados.",
        'I_double_negative_keep': "Nunca pedi para excluírem minha conta e não quero que ela seja excluída.",
        'J_hypothetical': "Se eu encerrar minha conta, perco os pontos de fidelidade acumulados?",
        'K_contradictory': "Apaguem minha conta, mas deixem meu perfil ativo para meu marido continuar comprando.",
        'L_delete_with_legal_retention': "Excluam minha conta e meus dados pessoais. Sei que as notas fiscais precisam ser guardadas por lei.",
    },
    'fr': {
        'A_delete_account': "Je souhaite la suppression définitive de mon compte et de toutes mes données personnelles.",
        'B_verify_prior': "J'ai déjà demandé la suppression de mon compte et reçu une confirmation. Pouvez-vous vérifier qu'il ne reste rien me concernant et terminer la suppression si besoin ?",
        'C_scoped_item': "Mon compte est fermé depuis longtemps, mais une pièce jointe que j'avais envoyée au support est toujours stockée. Merci de la supprimer.",
        'D_marketing_only': "Merci de ne plus m'envoyer d'offres promotionnelles. Je veux seulement me désabonner de la newsletter.",
        'E_export_keep': "Pourriez-vous m'envoyer une copie de mes données ? Je continue à utiliser mon compte.",
        'F_correction': "Mon adresse e-mail a changé, merci de la mettre à jour sur mon profil.",
        'G_login_keep': "Je n'arrive plus à me connecter. Aidez-moi à récupérer l'accès, mais ne fermez surtout pas mon compte.",
        'H_double_negative_delete': "Ce n'est pas que je veuille garder ce compte, bien au contraire : supprimez-le avec mes données.",
        'I_double_negative_keep': "Je n'ai jamais demandé la suppression de mon compte et je ne veux pas qu'il soit supprimé.",
        'J_hypothetical': "Si je supprimais mon compte, est-ce que mes factures resteraient accessibles ?",
        'K_contradictory': "Supprimez mon compte, mais laissez mon profil actif pour que ma fille puisse continuer à commander.",
        'L_delete_with_legal_retention': "Veuillez supprimer mon compte et mes données personnelles. Je comprends que certaines factures doivent être conservées pour des raisons légales.",
    },
    'ko': {
        'A_delete_account': "제 계정과 저에 관한 모든 개인 정보를 영구적으로 삭제해 주세요.",
        'B_verify_prior': "지난달에 계정 삭제를 요청했고 완료 안내를 받았습니다. 남아 있는 제 정보가 없는지 확인하고, 필요하면 삭제를 마무리해 주세요.",
        'C_scoped_item': "계정은 이미 해지했는데 예전에 요청한 데이터 내보내기 파일이 아직 다운로드됩니다. 그 파일을 삭제해 주세요.",
        'D_marketing_only': "광고성 이메일만 그만 보내 주세요. 계정은 그대로 두셔도 됩니다.",
        'E_export_keep': "제 데이터 사본을 보내 주실 수 있나요? 계정은 계속 사용할 예정입니다.",
        'F_correction': "전화번호가 바뀌었습니다. 프로필 정보를 수정해 주세요.",
        'G_login_keep': "로그인이 되지 않습니다. 접속을 복구하도록 도와주세요. 계정은 해지하지 마세요.",
        'H_double_negative_delete': "계정을 유지하고 싶은 게 아닙니다. 계정과 개인 정보를 삭제해 주세요.",
        'I_double_negative_keep': "저는 계정 삭제를 요청한 적이 없으며, 삭제되는 것을 원하지 않습니다.",
        'J_hypothetical': "만약 계정을 탈퇴하면 주문 내역도 함께 사라지나요?",
        'K_contradictory': "계정을 삭제해 주세요. 다만 가족이 계속 쓸 수 있도록 프로필은 활성 상태로 남겨 주세요.",
        'L_delete_with_legal_retention': "계정과 개인 정보를 삭제해 주세요. 세금 관련 영수증은 법에 따라 보관해야 한다는 점은 이해합니다.",
    },
    'ja': {
        'A_delete_account': "私のアカウントと、保存されているすべての個人情報を完全に削除してください。",
        'B_verify_prior': "先月アカウントの削除を依頼し、完了の連絡を受けました。私の情報が残っていないか確認し、必要であれば削除を完了してください。",
        'C_scoped_item': "アカウントはすでに解約しましたが、サポートに送った添付ファイルがまだ残っているようです。そのファイルを削除してください。",
        'D_marketing_only': "宣伝メールの配信だけ停止してください。アカウントはそのままで結構です。",
        'E_export_keep': "私のデータのコピーを送っていただけますか。アカウントは引き続き利用します。",
        'F_correction': "住所が変わったので、登録情報を更新してください。",
        'G_login_keep': "ログインできなくなりました。アクセスを復旧するのを手伝ってください。アカウントは解約しないでください。",
        'H_double_negative_delete': "アカウントを残したいわけではありません。アカウントと個人情報を削除してください。",
        'I_double_negative_keep': "アカウントの削除を依頼したことはありませんし、削除してほしくありません。",
        'J_hypothetical': "もしアカウントを退会したら、購入履歴も消えてしまいますか？",
        'K_contradictory': "アカウントを削除してください。ただし、家族が使い続けられるようにプロフィールは有効なままにしてください。",
        'L_delete_with_legal_retention': "アカウントと個人情報を削除してください。税務上の請求書は法律に従って保管が必要なことは理解しています。",
    },
}


ITEM_KEEP_ACCOUNT = {
    'en': "Please delete the export file of my data you generated last week, but keep my account open. I still shop here.",
    'es': "Borren, por favor, el archivo adjunto que envié al soporte, pero no cierren mi cuenta; la sigo usando.",
    'pt': "Removam o resultado de busca que mostra meu perfil antigo, mas mantenham minha conta ativa.",
    'fr': "Supprimez l'export de mes données que vous avez généré, mais gardez mon compte ouvert, je l'utilise encore.",
    'ko': "지원팀에 보낸 첨부 파일만 삭제해 주세요. 계정은 계속 사용할 거라 유지해 주세요.",
    'ja': "検索結果に出てくる古いプロフィールの表示だけ削除してください。アカウントはこのまま使い続けます。",
}


def cases():
    """[(case_key, language, scenario, text, expected, expected_label)]"""
    out = []
    rows = [(lang, scenario, text) for lang, texts in TEXTS.items() for scenario, text in texts.items()]
    rows += [(lang, 'M_item_keep_account', text) for lang, text in ITEM_KEEP_ACCOUNT.items()]
    for lang, scenario, text in rows:
        expected, label = SCENARIOS[scenario]
        if expected.endswith(ITEM):
            expected += ITEM_TARGETS[scenario][lang]
        out.append((f'{lang}:{scenario}', lang, scenario, text, expected, label))
    return out
