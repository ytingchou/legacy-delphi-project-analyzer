object frmOrderEntry: TfrmOrderEntry
  Caption = 'Order Entry'
  object edtCustomer: TEdit
    DataField = 'CUSTOMER_ID'
  end
  object gridOrders: TDBGrid
    DataSource = 'dsOrders'
  end
  object btnSearch: TButton
    Caption = 'Search'
    OnClick = btnSearchClick
  end
end
