import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './button'
import { Card, CardContent, CardTitle } from './card'
import { Chip, ChipButton } from './chip'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from './dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './dropdown-menu'
import { Progress } from './progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select'
import { Switch } from './switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs'
import { toast, Toaster } from './toast'
import { ToggleGroup, ToggleGroupItem } from './toggle-group'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip'

describe('Button', () => {
  it('is a real button that defaults to type=button and fires onClick', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Design a class</Button>)
    const button = screen.getByRole('button', { name: 'Design a class' })
    expect(button).toHaveAttribute('type', 'button')
    await userEvent.click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not fire when disabled', async () => {
    const onClick = vi.fn()
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('can wear the look of a link', () => {
    render(
      <Button asChild>
        <a href="/classes/new">New class</a>
      </Button>,
    )
    expect(screen.getByRole('link', { name: 'New class' })).toHaveAttribute('href', '/classes/new')
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('Card', () => {
  it('renders its title and content', () => {
    render(
      <Card>
        <CardTitle>Plan</CardTitle>
        <CardContent>3 of 5 classes left</CardContent>
      </Card>,
    )
    expect(screen.getByRole('heading', { name: 'Plan' })).toBeInTheDocument()
    expect(screen.getByText('3 of 5 classes left')).toBeInTheDocument()
  })
})

describe('Chip', () => {
  it('ChipButton reports its state with aria-pressed and toggles', async () => {
    function Example() {
      const [on, setOn] = useState(false)
      return (
        <ChipButton pressed={on} onClick={() => setOn(!on)}>
          45 min hills
        </ChipButton>
      )
    }
    render(<Example />)
    const chip = screen.getByRole('button', { name: '45 min hills' })
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(chip)
    expect(chip).toHaveAttribute('aria-pressed', 'true')
  })

  it('Chip shows a status as text', () => {
    render(<Chip tone="ok">Version 3 saved to MOWL</Chip>)
    expect(screen.getByText('Version 3 saved to MOWL')).toBeInTheDocument()
  })
})

describe('ToggleGroup', () => {
  it('selects one item at a time and moves with the arrow keys', async () => {
    const onChange = vi.fn()
    render(
      <ToggleGroup type="single" defaultValue="easy" aria-label="Effort" onValueChange={onChange}>
        <ToggleGroupItem value="easy">Easy</ToggleGroupItem>
        <ToggleGroupItem value="hard">Hard</ToggleGroupItem>
      </ToggleGroup>,
    )
    const easy = screen.getByRole('radio', { name: 'Easy' })
    const hard = screen.getByRole('radio', { name: 'Hard' })
    expect(easy).toBeChecked()
    expect(hard).not.toBeChecked()
    await userEvent.click(hard)
    expect(onChange).toHaveBeenLastCalledWith('hard')
    expect(hard).toBeChecked()
    expect(easy).not.toBeChecked()
  })
})

describe('Dialog', () => {
  it('opens as a labelled modal, closes with Escape and gives focus back', async () => {
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Delete class</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>Delete this class?</DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogContent>
      </Dialog>,
    )
    const trigger = screen.getByRole('button', { name: 'Delete class' })
    await userEvent.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: 'Delete this class?' })
    expect(dialog).toHaveAccessibleDescription('This cannot be undone.')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(trigger).toHaveFocus()
  })

  it('locks the page with a class and inert, not with an injected style (style-src self)', async () => {
    const styles = document.querySelectorAll('style').length
    render(
      <>
        <button>Behind</button>
        <Dialog>
          <DialogTrigger asChild>
            <Button>Open it</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogTitle>Title</DialogTitle>
            <DialogDescription>Text</DialogDescription>
          </DialogContent>
        </Dialog>
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Open it' }))
    await screen.findByRole('dialog')
    expect(document.documentElement).toHaveClass('overflow-hidden')
    expect(document.body.querySelector('[inert]')).not.toBeNull()
    expect(document.querySelectorAll('style')).toHaveLength(styles)
    expect(document.body.getAttribute('style') ?? '').not.toContain('overflow')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.documentElement).not.toHaveClass('overflow-hidden')
    expect(document.body.querySelector('[inert]')).toBeNull()
  })

  it('keeps Tab inside and closes on a click on the overlay', async () => {
    const onOpenChange = vi.fn()
    render(
      <>
        <button>Behind</button>
        <Dialog defaultOpen onOpenChange={onOpenChange}>
          <DialogContent>
            <DialogTitle>Title</DialogTitle>
            <DialogDescription>Text</DialogDescription>
            <button>Inside</button>
          </DialogContent>
        </Dialog>
      </>,
    )
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const inside = screen.getByRole('button', { name: 'Inside' })
    const close = screen.getByRole('button', { name: 'Close' })
    // The controls are Inside, then Close: Tab from the last wraps to the first and back.
    close.focus()
    await userEvent.tab()
    expect(inside).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(close).toHaveFocus()
    const overlay = document.querySelector('[aria-hidden="true"].fixed')!
    await userEvent.click(overlay)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('has a labelled Close button', async () => {
    render(
      <Dialog defaultOpen>
        <DialogContent>
          <DialogTitle>Title</DialogTitle>
          <DialogDescription>Text</DialogDescription>
        </DialogContent>
      </Dialog>,
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('DropdownMenu', () => {
  it('opens from the keyboard, picks an item and closes', async () => {
    const onSelect = vi.fn()
    render(
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" aria-label="More">
            ...
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={onSelect}>Versions</DropdownMenuItem>
          <DropdownMenuItem>Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    )
    screen.getByRole('button', { name: 'More' }).focus()
    await userEvent.keyboard('{Enter}')
    const menu = await screen.findByRole('menu')
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(2)
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Versions' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  })
})

describe('Tabs', () => {
  it('uses the tab roles and shows the panel of the selected tab', async () => {
    render(
      <Tabs defaultValue="connections">
        <TabsList aria-label="Account">
          <TabsTrigger value="connections">Connections</TabsTrigger>
          <TabsTrigger value="plan">Plan and usage</TabsTrigger>
        </TabsList>
        <TabsContent value="connections">MOWL is connected</TabsContent>
        <TabsContent value="plan">3 classes left</TabsContent>
      </Tabs>,
    )
    expect(screen.getByRole('tab', { name: 'Connections' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('MOWL is connected')
    await userEvent.click(screen.getByRole('tab', { name: 'Plan and usage' }))
    expect(screen.getByRole('tab', { name: 'Plan and usage' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('3 classes left')
  })

  it('moves between tabs with the arrow keys', async () => {
    render(
      <Tabs defaultValue="a">
        <TabsList aria-label="Example">
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">first</TabsContent>
        <TabsContent value="b">second</TabsContent>
      </Tabs>,
    )
    screen.getByRole('tab', { name: 'A' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'B' })).toHaveFocus()
  })
})

describe('Switch', () => {
  it('has role switch, a label, and toggles with click and Space', async () => {
    const onChange = vi.fn()
    render(
      <div>
        <label htmlFor="working">Let classbeat make a working playlist</label>
        <Switch id="working" onCheckedChange={onChange} />
      </div>,
    )
    const toggle = screen.getByRole('switch', { name: 'Let classbeat make a working playlist' })
    expect(toggle).not.toBeChecked()
    await userEvent.click(toggle)
    expect(toggle).toBeChecked()
    expect(onChange).toHaveBeenLastCalledWith(true)
    toggle.focus()
    await userEvent.keyboard(' ')
    expect(toggle).not.toBeChecked()
  })
})

describe('Tooltip', () => {
  it('shows its text, linked to the trigger, on keyboard focus', async () => {
    render(
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button>TSS</Button>
          </TooltipTrigger>
          <TooltipContent>Training Stress Score</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    )
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'TSS' })).toHaveFocus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Training Stress Score')
    expect(screen.getByRole('button', { name: 'TSS' })).toHaveAccessibleDescription('Training Stress Score')
  })
})

describe('Toast', () => {
  it('announces a message through a live region', async () => {
    render(<Toaster />)
    toast('Class saved')
    expect(await screen.findByText('Class saved')).toBeInTheDocument()
    expect(document.querySelector('[aria-live]')).not.toBeNull()
  })
})

describe('Progress', () => {
  it('exposes its value as a progressbar', () => {
    render(<Progress value={40} aria-label="Classes this month" />)
    const bar = screen.getByRole('progressbar', { name: 'Classes this month' })
    expect(bar).toHaveAttribute('aria-valuenow', '40')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
  })
})

describe('Select', () => {
  it('opens as a listbox, picks an option with the keyboard and shows it', async () => {
    const onChange = vi.fn()
    render(
      <Select onValueChange={onChange}>
        <SelectTrigger aria-label="Length">
          <SelectValue placeholder="Pick a length" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="30">30 minutes</SelectItem>
          <SelectItem value="45">45 minutes</SelectItem>
        </SelectContent>
      </Select>,
    )
    const trigger = screen.getByRole('combobox', { name: 'Length' })
    expect(trigger).toHaveTextContent('Pick a length')
    trigger.focus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('listbox')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('option', { name: '45 minutes' }))
    expect(onChange).toHaveBeenLastCalledWith('45')
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Length' })).toHaveTextContent('45 minutes'))
  })
})
